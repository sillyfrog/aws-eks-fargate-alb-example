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

So `kubectl` works with the cluster, each user will need to add the cluster to their context with the following command (assuming they already have the `aws` CLI configure):

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

There has to be a better way...

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

Note, this does not appear to delete the ALB, which you can remove manually in the AWS console under `EC2 > Load Balancers`.

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
