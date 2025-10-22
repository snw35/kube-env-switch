# kube-env-switch
Kubernetes operator that can alter ENV values at runtime in response to events.

## About

This is a [Kubernetes Operator](https://kubernetes.io/docs/concepts/extend-kubernetes/operator/) that can alter the environemnt varible (ENV) values of pods at runtime. It currently supports the following event types:

 * CrashLoopBackoff

This makes it suitable for the following example scenarios:

 * Attempting to recover a crashlooping deployment by:
    * Increasing scaling factors, such as thread-count or JVM memory size.
    * Turning certain feature-flags on or off.
 * Setting `DEBUG` level logging for crashlooping pods, and `ERROR` level logging otherwise.

## Requirements

To use this operator, you need to:

 * Deploy your application as pods on Kubernetes. They can be created through any of the standard methods (Deployment, StatefulSet, DaemonSet, Job, CronJob).
 * Have your service take configuration parameters from ENV values (instead of e.g a configmap).
 * Control scaling or features through those ENV values. This could be e.g JVM memory allocation for a Java/Spring app, the max number of goroutines for a golang app, or a feature-flag that turns on or off $(cool but possibly unstable feature X).

## How to Use

Clone the repo and open the `kube/envswitch.yaml` file. Customize the ENV variable values in the deployment at the bottom to set:
  * `ENV_PATCH_JSON` with the ENV you want to change, and the value you want to set it to.

You can configure the other ENV values to change the label selector and number of restarts to wait for, if desired.

Then annotate the target pods with the following kubernetes label:
 * `envswitch: true`

This should be placed like so:
```
spec:
  template:
    metadata:
      labels: { env-switch: "true" }
```

Now clone the repo and apply the deployment and dependencies:
```
git clone
cd kube
kubectl apply -f envswitch.yaml
```

You should see the `env-switch` pods start up in the `operator` namespace, identify the target pods, and begin watching them for events.

## End to End Testing

The `tests/end-to-end` directory contains a Dockerfile for a crashlooping container and associated deployment. This can be be used for a full end-to-end test, where it will:

 * Identify the annotated pods, and climb the resource tree to find their deployment.
 * Place a watcher on the deployoment's pods for the crashloop event.
 * Trigger the watcher when a crashloop happens.
 * Switch the ENV varible `FIX_ME` from 0 to 1 in the deployment spec.

This will cause newly created containers (e.g ones that come up after crashing once the above has happened) to inherit this new ENV value, which will cause them to stop crashlooping.

## Planned Features

This project was created to learn Kubernetes operator functionality, and initially supports crashloop events only. Depending on time and interest, I plan to add:

 * Helm Chart.
 * Reverting ENV changes when pods stabalize.
 * Marking pods as changed to prevent re-applies.
 * Setting LOG_LEVEL via ENV.
 * Allowing a scale range and increment for integer ENV changes.
 * Allowing a list of options to cycle through for string ENV changes.
 * More event types, like ImagePullBackoff.
