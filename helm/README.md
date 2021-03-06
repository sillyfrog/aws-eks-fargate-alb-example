# Getting Starting with Kubernetes on AWS in Fargate

Below are my notes and steps to get started with using Kubernetes (k8s) on AWS in Fargate, including getting the Application Load Balancer (ALB) working, dynamic DNS updates of Ingress routes, an the creation of certificates, and allocating them to the ingress services.

This configuration is using a local [Helm Chart](https://helm.sh/). Best I can tell Helm Charts are typically hosted and not included in the repo, however the template nature (which I personally find easier to understand and work with), and significantly better documentation, are making it my current favorite option for managing a K8s rollout.

## Required Resources

All the steps below I did on MacOS 12.1. It should be fine on any OS assuming you can use the cli in the same way. You need the following AWS tools installed:

- [AWS CLI] (https://aws.amazon.com/cli/), configured and talking with AWS
- [eksctl] (https://github.com/weaveworks/eksctl)
- [kubectl] (https://kubernetes.io/docs/reference/kubectl/overview/), see [here for install](https://kubernetes.io/docs/tasks/tools/)
- [Helm] (https://helm.sh/)
- Common shell utilities, including `curl`, `wget`, `grep`, `jq`
- An AWS account. My testing was done with an account with full access
- A [Route 53](https://console.aws.amazon.com/route53/v2/hostedzones) hosted domain name. You can probably use a sub-domain of a TLD you have registered elsewhere, but I had a spare TLD registered and hosted with AWS that I used.

All but the first of these can be installed on MacOS using [Homebrew](https://brew.sh/).

## Creating the Cluster

### Initial Setup

First thing is some initial setup, including selecting the name for the cluster and the AWS region.

**Important**: You should set your `aws` client to match your desired region by using `aws configure`.

```bash
export YOUR_CLUSTER_NAME=testing
export YOUR_REGION_CODE=us-west-1
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity | jq -r '.Account')
```

### Create the Cluster on Fargate

This will create the cluster in Fargate, note this can take a _long_ time, up to 20 minutes:

```bash
eksctl create cluster --name $YOUR_CLUSTER_NAME --region $YOUR_REGION_CODE --fargate
```

Then associate the IAM OIDC provider (required to support IAM service accounts):

```bash
eksctl utils associate-iam-oidc-provider --cluster $YOUR_CLUSTER_NAME --approve
```

### Prepare for the Load Balancer Controller

Next we'll prepare and install the load balancer controller. This talks with the ALB to create routes and get the traffic to the right place in your k8s cluster.

We need to create an IAM policy to allow the Load Balancer Controller to look at the cluster, and speak with AWS to create and manage the ALB.

This command will need updating to match the latest release version, see the [AWS Load Balancer Controller Releases](https://github.com/kubernetes-sigs/aws-load-balancer-controller/releases) to confirm. Version _v2.3.2_ is used in the example below:

```bash
curl -o iam_policy.json https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/v2.3.1/docs/install/iam_policy.json
```

Once downloaded, install the policy into AWS:

```bash
aws iam create-policy \
   --policy-name AWSLoadBalancerControllerIAMPolicy \
   --policy-document file://iam_policy.json
```

If you get an error on the above command because the policy already exists, you may need to update it. I'm not sure the best way to do this, but during my testing I went to the [IAM Console](https://console.aws.amazon.com/iamv2/home?region=us-west-1#/policies), searched for the policy named `AWSLoadBalancerControllerIAMPolicy`, and then selected Actions > Delete. However unless you are doing a major update, you shouldn't need to do this.

Now install the above policy into your cluster:

```bash
eksctl create iamserviceaccount \
  --cluster=$YOUR_CLUSTER_NAME \
  --namespace=kube-system \
  --name=aws-load-balancer-controller \
  --attach-policy-arn=arn:aws:iam::$AWS_ACCOUNT_ID:policy/AWSLoadBalancerControllerIAMPolicy \
  --override-existing-serviceaccounts \
  --approve
```

You can then verify the IAM Policy is installed by running either:

```bash
eksctl get iamserviceaccount --cluster $YOUR_CLUSTER_NAME --name aws-load-balancer-controller --namespace kube-system
```

```bash
kubectl get serviceaccount aws-load-balancer-controller --namespace kube-system
```

### Install the Load Balancer Controller

Documentation on the configuration and setup of the Elastic Load Balancer (ELB) using Ingress annotations is available here: https://kubernetes-sigs.github.io/aws-load-balancer-controller/v2.4/guide/ingress/annotations/ This is a useful resource for setting up health checks correctly.

Get ready to install the Helm Chart by installing the AWS EKS repo:

```bash
helm repo add eks https://aws.github.io/eks-charts
```

Next install the required Custom Resource Definitions (CRDs)

```bash
kubectl apply -k "github.com/aws/eks-charts/stable/aws-load-balancer-controller//crds?ref=master"
```

Get your cluster VPC ID, as this is required for the Helm Chart, and install the Chart:

```bash
export VPC_ID=$(aws cloudformation describe-stacks --stack-name eksctl-$YOUR_CLUSTER_NAME-cluster | jq -r '[.Stacks[0].Outputs[] | {key: .OutputKey, value: .OutputValue}] | from_entries' | jq -r '.VPC')
helm install aws-load-balancer-controller eks/aws-load-balancer-controller \
    --set clusterName=$YOUR_CLUSTER_NAME \
    --set serviceAccount.create=false \
    --set region=$YOUR_REGION_CODE \
    --set vpcId=$VPC_ID \
    --set serviceAccount.name=aws-load-balancer-controller \
    -n kube-system
```

Once that is done, you can check it's installed and running with:

```bash
kubectl get all -A -o wide
```

This shows you the status of the entire cluster. The first section is the pods, these should all be `Running`. On Fargte I found this can often take 2-3 minutes before the pods would start. For example:

```
NAMESPACE     NAME                                                READY   STATUS    RESTARTS   AGE
kube-system   pod/aws-load-balancer-controller-6fccf9b4c4-4fnp6   1/1     Running   0          89s
kube-system   pod/aws-load-balancer-controller-6fccf9b4c4-bxdqx   1/1     Running   0          89s
kube-system   pod/coredns-0000000c9b-000t8                        1/1     Running   0          12m
kube-system   pod/coredns-0000000c9b-000gg                        1/1     Running   0          12m

NAMESPACE     NAME                                        TYPE        CLUSTER-IP       EXTERNAL-IP   PORT(S)         AGE
default       service/kubernetes                          ClusterIP   10.100.0.1       <none>        443/TCP         23m
kube-system   service/aws-load-balancer-webhook-service   ClusterIP   10.100.240.164   <none>        443/TCP         90s
kube-system   service/kube-dns                            ClusterIP   10.100.0.10      <none>        53/UDP,53/TCP   23m

NAMESPACE     NAME                        DESIRED   CURRENT   READY   UP-TO-DATE   AVAILABLE   NODE SELECTOR   AGE
kube-system   daemonset.apps/aws-node     0         0         0       0            0           <none>          23m
kube-system   daemonset.apps/kube-proxy   0         0         0       0            0           <none>          23m

NAMESPACE     NAME                                           READY   UP-TO-DATE   AVAILABLE   AGE
kube-system   deployment.apps/aws-load-balancer-controller   2/2     2            2           91s
kube-system   deployment.apps/coredns                        2/2     2            2           23m

NAMESPACE     NAME                                                      DESIRED   CURRENT   READY   AGE
kube-system   replicaset.apps/aws-load-balancer-controller-6fccf9b4c4   2         2         2       92s
kube-system   replicaset.apps/coredns-0000000c9b                        2         2         2       12m
kube-system   replicaset.apps/coredns-00000005b                         0         0         0       23m
```

## Start an Application

### Create a Fargate Profile

To run an actual application, you'll need a Fargate profile created in the cluster on EKS. This needs to be created with the same namespace as the namespace used in your deployment, in this case, `whoami-demo`. The `--name` of the profile can differ from the `--namespace`, however I like to keep these the same to prevent confusion.

```bash
eksctl create fargateprofile --cluster $YOUR_CLUSTER_NAME --region $YOUR_REGION_CODE --name whoami-demo --namespace whoami-demo
```

### Start the Application!

If not already, change to the `/helm/` directory, and run the following command. This will use the chart default values:

```bash
helm upgrade --install demo ./helmchartdemo/
```

```
Release "demo" does not exist. Installing it now.
NAME: demo
LAST DEPLOYED: Sun Feb  6 13:20:46 2022
NAMESPACE: default
STATUS: deployed
REVISION: 1
TEST SUITE: None
```

Note that the command is actually `upgrade`, with an `--install` option. I'm not sure if there are other side effects, but so far running this has always worked, Helm figures out what it needs to do and does it (either install, or upgrade the running configuration).

To check the ingress deployment progress, and the assigned hostname, run:

```bash
kubectl get ingress/ingress-whoami -n whoami-demo
```

```
NAME             CLASS    HOSTS   ADDRESS                                                                   PORTS   AGE
ingress-whoami   <none>   *       k8s-whoamide-ingressw-0000000314-0000005147.us-west-1.elb.amazonaws.com   80      68s
```

Once that's complete (again, it may take a few minutes), you can browse to the above host name on HTTP, eg: http://k8s-whoamide-ingressw-0000000314-0000005147.us-west-1.elb.amazonaws.com/ .

The base URL will give you a nginx welcome page. You can also visit `/whoami` to see the output from the whoami container.

The whoami app gives plain text output showing the request, and the hops taken to get there.

If things aren't working right away, wait a further 15 minutes and try again, things like DNS TTL can also cause issues.

If you continue to have errors, check the logs for the load balancer with:

```bash
kubectl logs deployment.apps/aws-load-balancer-controller -n kube-system
```

More information on the Ingress configuration options, can be found at: https://kubernetes-sigs.github.io/aws-load-balancer-controller/v2.3/guide/ingress/annotations/

## Create a .yaml File for Values

To allow customization of the chart, we need to pass in a `.yaml` file using the `-f` option. Multiple `-f` options are allowed, with the later ones overriding the earlier ones. For this demo we'll just have a single file, that will override the values in the `values.yaml` file that forms part of the chart.

As a starting point, copy the `helmchartdemo/values.yaml` file to the current directory:

```bash
cp helmchartdemo/values.yaml config.yaml
```

Moving forward, we'll update the new `config.yaml` file with our cluster specific values.

## Automatic Public DNS Updates

To allow your cluster to update its own DNS, it needs an IAM policy, and the service account associated with the cluster. Your DNS must be hosted in Route 53.

This runs in it's own namespace, create a Fargate namespace profile:

```bash
eksctl create fargateprofile --cluster $YOUR_CLUSTER_NAME --region $YOUR_REGION_CODE --name external-dns --namespace external-dns
```

Create the policy:

```bash
aws iam create-policy \
 --policy-name AllowExternalDNSUpdates \
 --policy-document file://../AllowExternalDNSUpdates.json
```

As per above, if the policy exists, it may need to be deleted and re-created if it does not have the correct permissions. `AllowExternalDNSUpdates.json` is part of this repo.

Next associate the policy with a service account:

```
eksctl create iamserviceaccount \
 --cluster $YOUR_CLUSTER_NAME \
    --namespace=external-dns \
    --name=external-dns \
    --attach-policy-arn arn:aws:iam::$AWS_ACCOUNT_ID:policy/AllowExternalDNSUpdates \
 --override-existing-serviceaccounts \
 --approve
```

Next your `config.yaml` file needs to be updated with the ARN of the role create above. The ARN can be extracted with:

```bash
eksctl get iamserviceaccount --cluster $YOUR_CLUSTER_NAME --namespace=external-dns --name=external-dns --output=json | jq -r '.[0].status.roleARN'
```

Put the value output as a string under `externalDnsUpdateARN:` in your `config.yaml`.

Before the chart can be applied, the namespace has to be deleted because it was created by the `eksctl` command. Helm doesn't like this because it's not under it's control from the outset. The error looks like this:

```
Error: UPGRADE FAILED: rendered manifests contain a resource that already exists. Unable to continue with update: Namespace "external-dns" in namespace "" exists and cannot be imported into the current release: invalid ownership metadata; label validation error: missing key "app.kubernetes.io/managed-by": must be set to "Helm"; annotation validation error: missing key "meta.helm.sh/release-name": must be set to "demo"; annotation validation error: missing key "meta.helm.sh/release-namespace": must be set to "default"
```

The simplest solution I have found is just to delete it, and then do the upgrade. To delete it:

```bash
kubectl delete namespaces external-dns
```

Then apply the chart. You'll note that now the `externalDnsUpdateARN` value is now set, the new namespace will be created, and the external DNS provider pod will be created.

```bash
helm upgrade --install -f config.yaml demo ./helmchartdemo/
```

The progress of the pod creation can be seen with:

```bash
kubectl get pods -o wide -n external-dns
```

```
NAME                           READY   STATUS              RESTARTS   AGE   IP       NODE                                                   NOMINATED NODE   READINESS GATES
external-dns-0000075b4-mzrpv   0/1     ContainerCreating   0          77s   <none>   fargate-ip-192-168-168-95.us-west-1.compute.internal   <none>           <none>
```

When the state is `Running`, the pod is ready to start updating DNS entries.

### Set DNS Host Name

Next the DNS name to use needs to be set on the ingress configuration. Modify your `config.yaml` and set the `dnsHostname:` value to your desired domain name hosted in AWS Route 53, then save the file and apply.

```bash
helm upgrade --install -f config.yaml demo ./helmchartdemo/
```

Once it has finished applying, in 1-20 minutes, the DNS name should have updated, and you can go to it in your browser.

If you have issues, you can view the logs from the external-dns pod by running:

```bash
kubectl logs deployment.apps/external-dns -n external-dns -f
```

## HTTPS and Certificate Selection

To use HTTPS with the above configuration, firstly a certificate is required from AWS. This can be generated in the [ACM Console](https://console.aws.amazon.com/acm/home):

1. "Request" or "Request a Certificate"
1. "Request a public certificate"
1. "Next"
1. Enter your desired FQDN, and select "DNS validation"
1. "Request"
1. On the screen that comes up, you may need to reload to see the new certificate request
1. Click on the new Certificate
1. "Create records in Route 53"
1. "Create Records"

After 1-10 minutes the new certificate should be created and ready to use (Status: Issued).

Once it has been validated, modify `config.yaml` again, and set the domain to an entry under the `tlsHosts:` list. This is configured as a YAML list to show how different data types can be set in a customized configuration. For this demo, just uncomment the line with `demo.example.com`, and replace it with your domain (which will probably be the same as `dnsHostName` above). (YAML / Helm provide ways to reference other values already set in the file, see here for more information: https://helm.sh/docs/chart_template_guide/yaml_techniques/#yaml-anchors .)

```bash
helm upgrade --install -f config.yaml demo ./helmchartdemo/
```

You should now be able to go to the HTTPS version of your site at the domain configured above. Again, if things aren't working in 1-20 minutes, you can view the logs with:

```bash
kubectl logs deployment.apps/aws-load-balancer-controller -n kube-system -f
```

# Container Environment

This example also introduces a way to set environment variables in the container.

The values need to be set in the YAML file (because the chart can't access files outside it's own directories, and files passed in need to be YAML, and because Kubernetes is involved, there has to be something that everyone wants that's in no way simple).

At the end of the `config.yaml`, there is a `nginxEnv` section that came across from the default `values.yaml`. These key/value pairs from part of the _nginx_ environment. To access the container, and see the live config, run:

```bash
kubectl exec -n whoami-demo -it deployment.apps/nginx-deployment -- /bin/bash
```

Then when in the container, you can view the `DEMO_` env by running:

```bash
 env | grep DEMO_
```

You can then make changes to the `config.yaml` file in the `nginxEnv` section, and run an upgrade again so the changes take effect. After a few minutes (allow for the nginx k8s upgrade to complete), you can do the same thing again and view the new environment.

To see how this is configured, look in the `nginx.yaml` file - there is a trick here where the _configMap_ needs to be renamed (among other options) so the containers actually restart. Otherwise the _configMap_ will get updated, but the containers won't know about it. By using the `sha256sum`, it will create a unique name if there are ever any changes. Helm will then clean up the old configMap as it's no longer in use, and having a reference to the new name means k8s will restart the containers using the specified update strategy.

# Logging

To log from an EKS cluster, logging must be enabled. By default the logs will go to [AWS CloudWatch](https://aws.amazon.com/cloudwatch/).

To enable logging, modify `config.yaml`, and enter your AWS Region in the `awsLoggingRegion` section (eg: `us-west-1`). Then apply:

```bash
helm upgrade --install -f config.yaml demo ./helmchartdemo/
```

For this to work, it must also be given permission to log to CloudWatch. Firstly create the policy in your AWS account, as per before, if it's already created and needs to be updated, you may need to delete to re-create:

```bash
curl -o cloudwatch-permissions.json \
     https://raw.githubusercontent.com/aws-samples/amazon-eks-fluent-logging-examples/mainline/examples/fargate/cloudwatchlogs/permissions.json
aws iam create-policy \
        --policy-name FluentBitEKSFargate \
        --policy-document file://cloudwatch-permissions.json
```

Then apply the policy to the role profile:

```bash
export FARGATE_POD_EXECUTION_ROLE=$(eksctl get fargateprofile --cluster $YOUR_CLUSTER_NAME --region $YOUR_REGION_CODE --output json | jq -r '.[] | select(.name == "fp-default") | .podExecutionRoleARN' | cut -d "/" -f2)
aws iam attach-role-policy \
 --policy-arn arn:aws:iam::$AWS_ACCOUNT_ID:policy/FluentBitEKSFargate \
 --role-name $FARGATE_POD_EXECUTION_ROLE
```

After visiting a page hosted by nginx, you should start to see the logs show up in the CloudWatch Console with the group name `fluent-bit-cloudwatch`.

# Giving Additional Users Access

## Configuring `kubectl` Context

So `kubectl` works with the cluster, each user will need to add the cluster to their context with the following command (assuming they already have the `aws` CLI configured):

```bash
aws eks update-kubeconfig --region $YOUR_REGION_CODE --name $YOUR_CLUSTER_NAME
```

Then assuming they have permissions (see below), it should be possible to run:

```bash
kubectl get svc
```

And get something like:

```
NAME         TYPE        CLUSTER-IP   EXTERNAL-IP   PORT(S)   AGE
kubernetes   ClusterIP   10.100.0.1   <none>        443/TCP   23h
```

## Give Users Permissions

There has to be a better way... Turns out there is not yet, more info [here](https://github.com/aws/containers-roadmap/issues/185)

I found this page the most useful: https://aws.amazon.com/premiumsupport/knowledge-center/eks-api-server-unauthorized-error/

To allow using to gain access to the cluster, they need to be explicitly allowed, if not, they'll get an error such as:

```
error: You must be logged in to the server (Unauthorized)
```

The solution is for the cluster creator to run:

```bash
kubectl edit configmap aws-auth -n kube-system
```

Then modify the YAML to include the sections as per below in the `data:` section, as a new entry after `mapRoles`, update the `sampleuserX` values with your real user ARNs and usernames:

```yaml
mapUsers: |
  - userarn: arn:aws:iam::111111111111:user/sampleuser1
    username: sampleuser1
    groups:
      - system:masters
  - userarn: arn:aws:iam::111111111111:user/sampleuser2
    username: sampleuser2
    groups:
      - system:masters
```

# Creating Users and Groups for Better AWS IAM Integration

These steps show how to create IAM roles and groups, and have this apply to permissions in the AWS EKS Cluster. This is based on the document [here](https://dev.to/nextlinklabs/setting-up-kubernetes-user-access-using-aws-iam-1goh), but like most AWS/k8s documentation it's wrong and can't be used 1:1.

This test is creating a `eks-demo-admin` and a `eks-demo-developer` role/group

```bash
aws iam create-role --role-name eks-admin-role --output text --query 'Role.Arn'
```

# Secrets

If you want to provide secret information to your pods, you can store it in secrets. These should be managed outside the repo/helm chart so they are not committed with the rest of your code.

## Creating Secrets

To create simple secrets, you can do the following:

```bash
kubectl create secret generic --namespace whoami-demo aws --from-literal=AWS_ACCESS_KEY_ID=123456789 --from-literal=AWS_SECRET_ACCESS_KEY_ID=super-secret-thing
```

There are a number of different types of [secrets](https://kubernetes.io/docs/concepts/configuration/secret/), the other one is TLS. To create a TLS secret firstly you'll need some certificates, so generate some:

```bash
mkdir /tmp/certs
cd /tmp/certs
openssl req -x509 -newkey rsa:4096 -sha256 -days 36500 -nodes -keyout example.key -out example.crt -extensions san -config <(echo "[req]";
    echo distinguished_name=req;
    echo "[san]";
    ) -subj "/CN=example.com"
```

Then we can upload these secrets:

```bash
kubectl create secret tls demo-tls --namespace whoami-demo --cert=/tmp/certs/example.crt --key=/tmp/certs/example.key
```

Finally, to use these secrets, add an entry to the `config.yaml` as follows:

```yaml
setSecrets: true
```

And then apply:

```bash
helm upgrade --install -f config.yaml demo ./helmchartdemo/
```

It'll take a few minuets to apply, watch the progress with:

```bash
kubectl get pods -A --watch
```

When the new container is in the `Running` state again, you can then see the secrets in place:

```
kubectl exec -n whoami-demo -it deployment.apps/nginx-deployment -- /bin/bash
root@nginx-deployment-7c595db465-qtj79:/# env | grep AWS_
AWS_SECRET_ACCESS_KEY_ID=super-secret-thing
AWS_ACCESS_KEY_ID=123456789
root@nginx-deployment-7c595db465-qtj79:/# ls -lh /run/secrets/demo/
total 0
lrwxrwxrwx 1 root root 14 Feb  7 22:36 tls.crt -> ..data/tls.crt
lrwxrwxrwx 1 root root 14 Feb  7 22:36 tls.key -> ..data/tls.key
```

## Modifying Secrets

To modify an existing secret, for simple ones, I found the simplest way is to delete then re-create:

```bash
kubectl delete secrets --namespace zworkflows aws
kubectl create secret generic --namespace whoami-demo aws --from-literal=aws_access_key_id=123456789 --from-literal=aws_secret_access_key_id=NEW-super-secret-thing
```

Alternatively, you can edit the the secret in place with:

```bash
kubectl edit secret --namespace whoami-demo aws
```

**Note**: If you doing this, the values are _base64_ encoded. So you must get your new values, base64 encode, and then paste them in to the edit window. For example: `echo "some value" | base64`

For TLS secrets, again deleted and re-creating is the easiest way I have found.

The pods will then need to be restarted to get the new values, there are a number of ways to do this. Personally I change an unused `env` to a new value using the helm configmap style deployment as per the nginx deployment in this repo.

# Persistent Storage

Pods deployed on Fargate all use ephemeral storage (ie: it's deleted when the pod stops), so for services that need persistent storage, you need to create an EC2 instance and add it to the cluster.

The steps below create a single EC2 instance, add it to the cluster, give it a `taint` so no pods will be scheduled there, create storage for our container, and then mount it and use it in a pod.

The Helm Chart will deploy a MariaDB pod into the cluster.

## Add an EC2 instance to your k8s Cluster and Taint it

Adding an EC2 instance is as per below, note you can select the `node-type`, size of storage, min/max node count etc. The command below is a relatively low spec instance. See the `eksctl create nodegroup --help` for more information on the options.

```bash
eksctl create nodegroup --cluster $YOUR_CLUSTER_NAME --region $YOUR_REGION_CODE --name ec2-persist --node-type t2.medium --node-volume-size 40 --nodes-min 1 --nodes-max 1
```

**Take note of the node**. This will be in the output of the above command, for example:

```
2022-02-09 13:05:17 [???]  node "ip-192-168-41-129.us-west-1.compute.internal" is ready
```

If the node name was not recorded, it can also be extracted with:

```bash
kubectl get nodes --selector 'alpha.eksctl.io/nodegroup-name=ec2-persist' -o json | jq -r '.items[0].metadata.name'
```

To taint the server, run the following command, this will set the `NoSchedule` taint which will prevent anything getting scheduled on it, unless explicitly configure to do so:

```bash
kubectl taint node --selector 'alpha.eksctl.io/nodegroup-name=ec2-persist' aws=ec2:NoSchedule
```

## Create Storage

The EBS storage volume needs to be created _before_ the pod is started. Below generates a 10GB volume. See `aws ec2 create-volume help` for more information on all the options and their meaning available for the command.

The AZ of the EC2 instance that was created above needs to be entered, this is set in the `EC2_AZ` environment variable. The volume is created with a tag of `name=demo-mariadb` it can be located later.

```bash
export EC2_AZ=$(kubectl get nodes --selector 'alpha.eksctl.io/nodegroup-name=ec2-persist' -o json | jq -r '.items[0].metadata.labels["topology.kubernetes.io/zone"]')
aws ec2 create-volume --availability-zone=$EC2_AZ --size=10 --volume-type=gp2 --tag-specifications 'ResourceType=volume,Tags=[{Key=name,Value=demo-mariadb}]'
```

## Create Pod with Mounted Storage

Finally, get the Volume ID of the storage volume just created. This can be done with:

```bash
aws ec2 describe-volumes --filters --filters Name=tag:name,Values=demo-mariadb | jq -r '.Volumes[0].VolumeId'
```

Put that value on a new line in `config.yaml` with the key `mariadbVolumeId`, for example:

```
mariadbVolumeId: vol-01234567892048a9e
```

Deploy those changes:

```bash
helm upgrade --install -f config.yaml demo ./helmchartdemo/
```

## Testing

To test the storage remains even when the pod is deleted, see the following commands and output

```
bash-3.2$ kubectl exec -n mariadb -it mariadb -- /bin/bash

root@mariadb:/# mysql --user=root --password=password --database=database
MariaDB [database]> CREATE TABLE test(f int);
Query OK, 0 rows affected (0.013 sec)

MariaDB [database]> CREATE TABLE test(f int);
ERROR 1050 (42S01): Table 'test' already exists
MariaDB [database]> Bye
root@mariadb:/# exit

bash-3.2$ kubectl delete pod mariadb -n mariadb
pod "mariadb" deleted

bash-3.2$ helm upgrade --install -f config.yaml demo ./helmchartdemo/
Release "demo" has been upgraded. Happy Helming!
NAME: demo
LAST DEPLOYED: Wed Feb  9 15:54:40 2022
NAMESPACE: default
STATUS: deployed
REVISION: 25
TEST SUITE: None

bash-3.2$ kubectl exec -n mariadb -it mariadb -- /bin/bash
root@mariadb:/# mysql --user=root --password=password --database=database
MariaDB [database]> CREATE TABLE test(f int);
ERROR 1050 (42S01): Table 'test' already exists
MariaDB [database]>

```

# Kubernetes Dashboard

Kubernetes has a [standard dashboard](https://kubernetes.io/docs/tasks/access-application-cluster/web-ui-dashboard/) to log and view statics of the running cluster.

To install and start the dashboard using the standard configuration, a fargate profile needs to be created (note the namespace used must match that required by the dashboard), and the dashboard deployed:

```bash
eksctl create fargateprofile --cluster $YOUR_CLUSTER_NAME --region $YOUR_REGION_CODE --name kubernetes-dashboard --namespace kubernetes-dashboard
kubectl apply -f https://raw.githubusercontent.com/kubernetes/dashboard/v2.4.0/aio/deploy/recommended.yaml
```

For how to access the dashboard, see the [Kubernetes Docs](https://kubernetes.io/docs/tasks/access-application-cluster/web-ui-dashboard/).

# Changing Clusters / "Contexts"

If you are working on several clusters at once, you can change which one `kubectl` is using.

To see what contexts are available:

```bash
kubectl config get-contexts
```

```
CURRENT   NAME                                   CLUSTER                           AUTHINFO                                    NAMESPACE
          docker-desktop                         docker-desktop                    docker-desktop
          user@dev.ap-southeast-2.eksctl.io      dev.ap-southeast-2.eksctl.io      user@dev.ap-southeast-2.eksctl.io
*         user@testing-helm.us-west-1.eksctl.io  testing-helm.us-west-1.eksctl.io  user@testing-helm.us-west-1.eksctl.io
```

The **\*** indicates the currently active context. To change, for example run:

```bash
kubectl config use-context user@dev.ap-southeast-2.eksctl.io
```

# Accessing Services in the Cluster

A running deployment/pod in the cluster can access other services in the cluster, even if they are in a different name space.

To access a service that is in the _same_ name space, the name of the service can be used, for example:

```bash
curl nginx-service
```

To access a server that's in a _different_ namespace, an explicit internal DNS entry is created, for example:

```bash
curl nginx-service.whoami-demo.svc.cluster.local
```

This is built up of `<service name>.<namespace>.svc.cluster.local`.

# Further Debugging

## Pod not Starting

If a pod is not starting for some reason, the full logs can be viewed with `describe` for example, a pod is stuck in the _ContainerCreating_ state:

```
# kubectl get pods -A                                                                                                                                                                                          (helm-chart)zworkflows
NAMESPACE      NAME                                            READY   STATUS              RESTARTS   AGE
external-dns   external-dns-ff5ff75b4-9sjjh                    1/1     Running             0          40h
kube-system    aws-load-balancer-controller-676b8764cd-cfqh5   1/1     Running             0          41h
kube-system    aws-load-balancer-controller-676b8764cd-plxs4   1/1     Running             0          41h
kube-system    coredns-65c6c5fc9b-m8lxc                        1/1     Running             0          41h
kube-system    coredns-65c6c5fc9b-wq7jf                        1/1     Running             0          41h
whoami-demo    nginx-deployment-75f9b99c46-w48gj               0/1     ContainerCreating   0          2m52s
whoami-demo    nginx-deployment-7c595db465-qtj79               1/1     Running             0          57m
whoami-demo    whoami-84c64bb8d8-dst56                         1/1     Running             0          40h
whoami-demo    whoami-84c64bb8d8-krlkw                         1/1     Running             0          40h

# kubectl describe pod -n whoami-demo nginx-deployment-75f9b99c46-w48gj                                                                                                                                         (helm-chart)zworkflows
Name:                 nginx-deployment-75f9b99c46-w48gj
Namespace:            whoami-demo
Priority:             2000001000
Priority Class Name:  system-node-critical
Node:                 fargate-ip-192-168-148-89.us-west-1.compute.internal/192.168.148.89
Start Time:           Tue, 08 Feb 2022 09:31:22 +1000
Labels:               app=nginx-app
                      eks.amazonaws.com/fargate-profile=whoami-demo
                      pod-template-hash=75f9b99c46
Annotations:          CapacityProvisioned: 0.25vCPU 0.5GB
                      Logging: LoggingEnabled
                      kubernetes.io/psp: eks.privileged
Status:               Pending
IP:
IPs:                  <none>
Controlled By:        ReplicaSet/nginx-deployment-75f9b99c46
Containers:
  nginx:
    Container ID:
    Image:          public.ecr.aws/nginx/nginx:1.21
    Image ID:
    Port:           80/TCP
    Host Port:      0/TCP
    State:          Waiting
      Reason:       ContainerCreating
    Ready:          False
    Restart Count:  0
    Requests:
      cpu:     200m
      memory:  100Mi
    Environment Variables from:
      example-config-012e52da  ConfigMap  Optional: false
      aws                      Secret     Optional: false
    Environment:               <none>
    Mounts:
      /run/secrets/demo from demo-tls (ro)
      /var/run/secrets/kubernetes.io/serviceaccount from kube-api-access-fcwl4 (ro)
Conditions:
  Type              Status
  Initialized       True
  Ready             False
  ContainersReady   False
  PodScheduled      True
Volumes:
  demo-tls:
    Type:        Secret (a volume populated by a Secret)
    SecretName:  demo-tls
    Optional:    false
  kube-api-access-fcwl4:
    Type:                    Projected (a volume that contains injected data from multiple sources)
    TokenExpirationSeconds:  3607
    ConfigMapName:           kube-root-ca.crt
    ConfigMapOptional:       <nil>
    DownwardAPI:             true
QoS Class:                   Burstable
Node-Selectors:              <none>
Tolerations:                 node.kubernetes.io/not-ready:NoExecute op=Exists for 300s
                             node.kubernetes.io/unreachable:NoExecute op=Exists for 300s
Events:
  Type     Reason          Age                  From               Message
  ----     ------          ----                 ----               -------
  Normal   LoggingEnabled  4m4s                 fargate-scheduler  Successfully enabled logging for pod
  Normal   Scheduled       3m19s                fargate-scheduler  Successfully assigned whoami-demo/nginx-deployment-75f9b99c46-w48gj to fargate-ip-192-168-148-89.us-west-1.compute.internal
  Warning  FailedMount     76s                  kubelet            Unable to attach or mount volumes: unmounted volumes=[demo-tls], unattached volumes=[demo-tls kube-api-access-fcwl4]: timed out waiting for the condition
  Warning  FailedMount     71s (x9 over 3m19s)  kubelet            MountVolume.SetUp failed for volume "demo-tls" : secret "demo-tls" not found
```

At the end, the Warning `FailedMount` shows that the expected secret is not there, if we add the secret (as per secrets above), the pod should then start automatically (if not, view describe again to see the next error).

## Error about "Namespace Exists"

An error such as the following may mean the `kubectl` context is set to a specific namespace:

```
Error: rendered manifests contain a resource that already exists. Unable to continue with install: Namespace "external-dns" in namespace "" exists and cannot be imported into the current release: invalid ownership metadata; annotation validation error: key "meta.helm.sh/release-namespace" must equal "random-namespace": current value is "default"
helm.go:84: [debug] Namespace "external-dns" in namespace "" exists and cannot be imported into the current release: invalid ownership metadata; annotation validation error: key "meta.helm.sh/release-namespace" must equal "random-namespace": current value is "default"
rendered manifests contain a resource that already exists. Unable to continue with install
```

To confirm, see if there is a namespace set in the context by running:

```bash
kubectl config get-contexts
```

```
CURRENT   NAME                                    CLUSTER                            AUTHINFO                               NAMESPACE
          docker-desktop                          docker-desktop                     docker-desktop
*         user@testing-helm.us-west-1.eksctl.io   testing-helm.us-west-1.eksctl.io   user@testing-helm.us-west-1.eksctl.io  random-namespace
```

Note that in the above output, the current (the line starting with the `*`) context has a namespace assigned.

To remove this, run the follwing:

```bash
kubectl config set-context --current --namespace=""
```

The Helm deployment should then work as expected.

# Final Cleanup

If you are done with the deployments that were just installed using Helm, you can do an `uninstall` as follows:

```bash
helm uninstall demo
```

If you are done with the cluster, you can destroy it completely with `eksctl`:

```bash
eksctl delete cluster --name $YOUR_CLUSTER_NAME
```

This can take upwards of 20 minutes.

Note, this does not appear to always delete the ALB, which you can remove manually in the AWS console under `EC2 > Load Balancers`.

# Pod Size and Resource Allocation

As per the [AWS Fargate Docs](https://docs.aws.amazon.com/eks/latest/userguide/fargate-pod-configuration.html), each pod is deployed in their own dedicated node. The size of this node defaults to .25 vCPU and 0.5GB of RAM. To get a node with more resources, the Deployment should including the desired size as part of the `spec` template section.

The available pod/node sizes are listed in the docs as per above. **Remember**: for the `memory` size request, AWS will add 256MB before creating the node, so if you request 1vCPU, and 2GB of RAM, you'll get a pod with 1vCPU, and 3GB of RAM (as that's the closest next size).

To adjust the size of the pod/node in the example `whoami.yaml`, go to the `kind: Deployment` section, and find the the `spec.template.spec.containers.resources` entries. The default is:

```yaml
cpu: 100m
memory: 100Mi
```

**Note**: `100m` means 0.1 vCPU (100 milli vCPU), see the [k8s docs](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/) for more info.

To increase this so we are allocated 2vCPU, and 4GB of RAM, you could change it to:

```yaml
cpu: "2"
memory: "3.75Gi"
```

Note the `3.75Gi` rather than `4Gi` to allow for the automatic overhead AWS applies for the `kubelet` etc. This means your process would not get the full `4Gi` either, rather at least the `3.75Gi`.

## View the Allocated Resources

To view the resources a pod's node has, can be done using the `kubectl describe pod`, firstly get the name of the `pod`:

```bash
kubectl get pods -o wide -n whoami-demo
```

```
NAME                    READY   STATUS    RESTARTS   AGE     IP                NODE                                                    NOMINATED NODE   READINESS GATES
whoami-f999d5cd-dgqjg   1/1     Running   0          3m42s   192.168.141.120   fargate-ip-192-168-141-120.us-west-1.compute.internal   <none>           <none>
whoami-f999d5cd-dm8hg   1/1     Running   0          2m39s   192.168.156.60    fargate-ip-192-168-156-60.us-west-1.compute.internal    <none>           <none>
```

Then, do a describe, and search for `CapacityProvisioned`, eg:

```bash
kubectl describe pod -n whoami-demo whoami-f999d5cd-dm8hg | grep CapacityProvisioned
```

```
Annotations:          CapacityProvisioned: 2vCPU 4GB
```

Here we can see we have a node with 2vCPU and 4GB as expected.
