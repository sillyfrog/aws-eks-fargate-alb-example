# Getting Starting with Kubernetes on AWS in Fargate

Below are my notes and steps to get started with using Kubernetes (k8s) on AWS in Fargate, including getting the Application Load Balancer (ALB) working, dynamic DNS updates of Ingress routes, an the creation of certificates, and allocating them to the ingress services.

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

Get ready to install the Helm Chart by install the AWS EKS repo:

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
kubectl get all -A
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

With the `whoami.yaml` file in the in the current directory run:

```bash
kubectl apply -f whoami.yaml
```

```
namespace/whoami-demo created
deployment.apps/whoami created
service/whoami created
ingress.networking.k8s.io/ingress-whoami created
```

To check the ingress deployment progress, and the assigned hostname, run:

```bash
kubectl get ingress/ingress-whoami -n whoami-demo
```

```
NAME             CLASS    HOSTS   ADDRESS                                                                   PORTS   AGE
ingress-whoami   <none>   *       k8s-whoamide-ingressw-0000000314-0000005147.us-west-1.elb.amazonaws.com   80      68s
```

Once that's complete (again, it may take a few minutes), you can browse to the above host name on HTTP, eg: http://k8s-whoamide-ingressw-0000000314-0000005147.us-west-1.elb.amazonaws.com/ .

The whoami app gives plain text output showing the request, and the hops taken to get there.

If things aren't working right away, wait a further 15 minutes and try again, things like DNS TTL can also cause issues.

If you continue to have errors, check the logs for the load balancer with:

```bash
kubectl logs deployment.apps/aws-load-balancer-controller -n kube-system
```

More information on the Ingress configuration options, can be found at: https://kubernetes-sigs.github.io/aws-load-balancer-controller/v2.3/guide/ingress/annotations/

## Automatic Public DNS Updates

To allow your cluster to update its own DNS, firstly it needs an IAM policy, and the service account associated with the cluster. Your DNS must be hosted in Route 53.

Create the policy:

```bash
aws iam create-policy \
 --policy-name AllowExternalDNSUpdates \
 --policy-document file://AllowExternalDNSUpdates.json
```

As per above, if the policy exists, it may need to be deleted and re-created. `AllowExternalDNSUpdates.json` is part of this repo.

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

This runs in it's own namespace, create a Fargate namespace profile:

```bash
eksctl create fargateprofile --cluster $YOUR_CLUSTER_NAME --region $YOUR_REGION_CODE --name external-dns --namespace external-dns
```

Next the `external-dns.yaml` file needs to be updated with the ARN of the role create above. The ARN can be extracted with:

```bash
eksctl get iamserviceaccount --cluster $YOUR_CLUSTER_NAME --namespace=external-dns --name=external-dns --output=json | jq -r '.[0].status.roleARN'
```

Put the value output (startwith with `arn:` and ending with random uppercase characters) where indicated it `external-dns.yaml`

Then apply the file:

```bash
kubectl apply -f external-dns.yaml
```

The progress of the pod creation can be seen with:

```bash
kubectl get pods -o wide -n external-dns
```

```
NAME                           READY   STATUS              RESTARTS   AGE   IP       NODE                                                   NOMINATED NODE   READINESS GATES
external-dns-0000075b4-mzrpv   0/1     ContainerCreating   0          77s   <none>   fargate-ip-192-168-168-95.us-west-1.compute.internal   <none>           <none>
```

When the state is `Running`, the pod is ready to start updating.

### Set DNS Host Name

Next the DNS name to use needs to be set on the ingress configuration. Modify the `whoami.yaml` file and look for the line with `external-dns.alpha.kubernetes.io/hostname: `. Uncomment that line and replace `demo.example.com` with your desired domain name hosted in AWS Route 53, then save the file and apply.

```bash
kubectl apply -f whoami.yaml
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

Once it has been validated, modify `whoami.yaml` again, and uncomment the line with `alb.ingress.kubernetes.io/listen-ports:` that includes the `{"HTTPS":443}` section, and comment out the existing `alb.ingress.kubernetes.io/listen-ports:` line. Also uncomment the `tls:` line, and the following 2 lines. Also replace the host with your HTTPS certificate host. The `spec.tls.hosts` section is used by the ALB to select the certificate to use.

Again, if things aren't working in 1-20 minutes, you can view the logs with:

```bash
kubectl logs deployment.apps/aws-load-balancer-controller -n kube-system -f
```
