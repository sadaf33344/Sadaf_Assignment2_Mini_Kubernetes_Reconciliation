
# COSC 3657 — Unit 2: Mini-Kubernetes Reconciliation Controller

**Project:** Mini-Kubernetes Reconciliation Controller  
**Course:** COSC 3657 — Distributed Systems  
**Implementation:** Python and Kubernetes  
**Test environment:** Windows, VS Code, WSL 2, Docker Desktop, and a local single-node `kind`-type Kubernetes cluster

## 1. Project overview

This project implements a small Kubernetes-style reconciliation controller in Python. The user declares the **desired state** of an application in a Kubernetes custom resource, and the controller compares that state with the **actual state** of the running Pods. When the numbers differ, it creates or removes Pods until they match. The controller also detects when Pods use an outdated container image and replaces them.

For example, if the desired state is three Pods but only two exist, the controller detects a **drift of +1** and creates one replacement. When the actual and desired counts are the same, the system has reached **equilibrium**.

The main objective is to demonstrate the Kubernetes controller pattern, event watching, declarative management, drift calculation, idempotent reconciliation, and recovery from Pod deletion.

## 2. Files in this repository

| File | Purpose |
| --- | --- |
| `reconciler.py` | The Python reconciliation controller: watches events, checks drift, creates/deletes Pods, and handles image changes. |
| `crd_definition.yaml` | Registers the `ResilientDeployment` custom resource type with Kubernetes. |
| `sample_cr.yaml` | Defines the `echo-service` application, desired replica count, and container image. |
| `README.md` | Explains project setup, commands, code behavior, tests, and evidence. |
| `AIA-Artificial-Intelligence-Aided.png` | AI-assistance disclosure badge supplied with the course materials. |
| `Screenshots/` | Screenshot evidence of controller tests. Keep screenshots in this folder before submitting to GitHub. |

## 3. How the controller works

1. The controller uses the Python Kubernetes client to connect to the current cluster using the local kubeconfig.
2. It reads the `ResilientDeployment` custom resource in the `default` namespace.
3. It finds Pods labeled `app.kubernetes.io/managed-by=echo-service` and excludes Pods being deleted and Pods whose phase is `Failed` or `Succeeded`.
4. It calculates **drift = desired replica count − observed active Pod count**.
5. If drift is positive, the controller creates missing Pods. If drift is negative, it removes extra Pods, choosing the newest Pods first.
6. If the counts match, it checks each Pod's image against the desired image. It deletes Pods using an outdated image so they can be replaced.
7. Watch threads listen for Pod and custom-resource changes. The main loop also checks again after a one-second timeout.
8. When there are no required changes, the controller takes no action. This is the **idempotent** behavior expected from a reconciliation loop.

### Important Python functions

- `create_pod()` creates a Pod with a generated name, identifying labels, an owner reference, and the requested container image.
- `reconcile_all()` calculates drift and performs scale-up, scale-down, or image replacement.
- `watch_for_changes()` listens for API events and signals that the controller should check the state again.
- `main()` connects to Kubernetes, starts the two watcher threads, and runs the reconciliation loop until stopped with **Ctrl+C**.

## 4. Requirements and local environment

- Windows with **WSL 2** enabled
- **Docker Desktop** running with a working Kubernetes cluster
- **Python 3** and the Python Kubernetes client (`pip install kubernetes`)
- **kubectl** available in the VS Code PowerShell terminal
- A local kubeconfig pointing to the cluster being tested

The implementation was tested with Docker Desktop's built-in **single-node `kind`-type cluster**, showing one Ready control-plane node. Docker Desktop's Kubernetes version shown during testing was **v1.36.1**. The cluster was created through the Docker Desktop interface rather than through `kind create cluster --name cosc3657-unit2`.

**Note about the course guide:** The handout describes a “multi-node” cluster but gives the command `kind create cluster --name cosc3657-unit2`. With no additional configuration, that command also creates a **single-node** cluster by default. A true multi-node kind cluster requires an explicit node configuration. The local cluster in this project is documented as single-node, not multi-node.

To check that Docker and Kubernetes are available:

```powershell
docker info
kubectl get nodes
kubectl get pods -A
```

The system Pods were verified in the `Running` state. The single node showed `Ready`.

## 5. Setup and run the project

Open the project folder in VS Code and use its integrated PowerShell terminal. Make sure Docker Desktop and its Kubernetes cluster are running.

### Step 1 — Register the custom resource definition

```powershell
kubectl apply -f crd_definition.yaml
kubectl get crd resilientdeployments.cosc3657.tu.ca
```

The apply command was observed to create `resilientdeployments.cosc3657.tu.ca` successfully. The separate `get crd` verification command is included because it appears in the professor's instructions.

### Step 2 — Apply the sample custom resource

```powershell
kubectl apply -f sample_cr.yaml
kubectl get resilientdeployment echo-service -o yaml
```

The project uses the custom resource name **`echo-service`**. The professor's sample logs use `echo-cluster`; the different name is intentional and must be kept consistent with this project's YAML and commands.

To reproduce the *initial* test, use the following desired values in `sample_cr.yaml`:

```yaml
apiVersion: cosc3657.tu.ca/v1alpha1
kind: ResilientDeployment
metadata:
  name: echo-service
  namespace: default
spec:
  replicas: 3
  image: hashicorp/http-echo:latest
```

**Current test state:** After subsequent tests, `sample_cr.yaml` was changed to **2 replicas** with image **`nginx:alpine`**. This change was deliberate for testing and is not the initial three-replica example.

### Step 3 — Start the controller in Terminal 1

```powershell
python reconciler.py
```

Expected startup message:

```text
=== COSC 3657 Custom Kubernetes Reconciler Started ===
```

Keep this terminal running. In a second terminal, check the application Pods:

```powershell
kubectl get pods -l app.kubernetes.io/managed-by=echo-service
```

## 6. Tests and observed results

| Test | Action | Observed outcome | Status |
| --- | --- | --- | --- |
| Initial reconciliation | Applied a custom resource requesting 3 Pods | 3 Pods created; final `Desired: 3`, `Observed: 3`, `Drift: 0` | **Passed** |
| Automatic recovery after normal deletion | Deleted `echo-service-pv48g` using a normal `kubectl delete pod` | Reconciler detected 2 instead of 3 Pods and created `echo-service-rwr24`; returned to 3 Pods | **Passed** |
| Scale-up | Changed replicas from 3 to 5 and re-applied `sample_cr.yaml` | 2 additional Pods created; final `Desired: 5`, `Observed: 5`, `Drift: 0` | **Passed** |
| Scale-down | Changed replicas from 5 to 2 and re-applied `sample_cr.yaml` | 3 extra Pods removed; final `Desired: 2`, `Observed: 2`, `Drift: 0` | **Passed** |
| Container image change | Changed image from `hashicorp/http-echo:latest` to `nginx:alpine` with 2 replicas | `[UPDATE]` message, old Pods removed, and 2 replacements created; new Pods reported running | **Passed** |
| **Forced deletion and self-healing** | Force-deleted Pod `echo-service-5rxdg` with `--grace-period=0 --force` | Reconciler detected `Desired: 2`, `Observed: 1`, `Drift: 1`, created `echo-service-9l5kv`, and returned to `Drift: 0`; both Pods running | **Passed** |

### Evidence from the controller

The normal-deletion recovery test produced the following sequence:

```text
[RECONCILE LOOP] Resource: echo-service | Desired: 3 | Observed: 2 | Drift: 1
Scale-Up Event: Creating 1 Pod(s)...
[CREATE] echo-service-rwr24
[RECONCILE LOOP] Resource: echo-service | Desired: 3 | Observed: 3 | Drift: 0
EQUILIBRIUM REACHED for echo-service.
```

The scale-down and image-update logs showed the same basic process: detect the difference, take corrective action, and return to **drift 0**. The forced-deletion test followed this same pattern.

### Forced-deletion test (passed)

The professor specifically asks for a forced Pod deletion rather than only a normal deletion. This test was completed in the local cluster. In PowerShell, I selected one Pod managed by `echo-service` and deleted it with no grace period:

```powershell
$pod = kubectl get pods -l "app.kubernetes.io/managed-by=echo-service" -o jsonpath="{.items[0].metadata.name}"
kubectl delete pod $pod --grace-period=0 --force
kubectl get pods -l "app.kubernetes.io/managed-by=echo-service"
```

The terminal reported `pod "echo-service-5rxdg" force deleted from default namespace`. The controller log then showed:

```text
[RECONCILE LOOP] Resource: echo-service | Desired: 2 | Observed: 1 | Drift: 1
Scale-Up Event: Creating 1 Pod(s)...
[CREATE] echo-service-9l5kv
[RECONCILE LOOP] Resource: echo-service | Desired: 2 | Observed: 2 | Drift: 0
EQUILIBRIUM REACHED for echo-service.
```

A subsequent Pod listing showed two Pods in `Running` status. This verifies the required automatic recovery from forced deletion. Evidence is available in the terminal screenshots of the deletion command and reconciler logs.

### Declarative scale test in the professor's instructions

The professor's example uses a `kubectl patch` command to change replicas from 3 to 5. This project already demonstrated the **same declarative scaling behavior** by editing `sample_cr.yaml` and applying it with `kubectl apply -f sample_cr.yaml`, without restarting `reconciler.py`. If the exact patch command is required, that should be demonstrated separately.

## 7. Screenshot evidence

Screenshots recorded during development include initial Pod creation, normal deletion and replacement, scaling up and down, image updates, and the required forced-deletion recovery. Use the actual saved filenames and put the PNGs in the project's `Screenshots/` folder before uploading to GitHub. Suggested filenames:

- `Reconciliation_Test_Results.png` — earlier self-healing and scaling controller logs
- `Pod_Image_Before_Update.png` — original `hashicorp/http-echo:latest` image
- `Pod_Image_After_Update.png` — updated image `nginx:alpine`, if saved
- `Image_Update_Reconciler_Log.png` — image replacement and equilibrium
- `Forced_Deletion_Recovery_Test.png` — Python log showing forced deletion detected and a replacement created
- `Forced_Deletion_Command.png` — optional extra evidence showing `--force` deletion command and two Pods running; useful if available

The forced-deletion test is verified by the command output and reconciler screenshots. If only one screenshot is submitted, prioritize the reconciler log; the command screenshot gives clearer proof that `--force` was used.

## 8. Challenges encountered and solutions

| Challenge | What I did |
| --- | --- |
| Docker Desktop could not start because virtualization-related Windows features were disabled | Enabled **Virtual Machine Platform** and **Windows Subsystem for Linux**, then restarted Windows and updated WSL. |
| Docker commands were not initially recognized in VS Code | Restarted VS Code after installing Docker Desktop, then verified `docker --version` and `docker info`. |
| YAML parsing error while changing replicas | Corrected the YAML indentation so `replicas:` and `image:` aligned under `spec:`, saved, and re-applied the file. |
| Needed to show that the controller actually repaired failures | Checked both the controller log and `kubectl get pods` output to demonstrate Pod replacement and `Drift: 0`. |
| Needed to understand reconciliation | Used `Desired`, `Observed`, and `Drift` values in the logs to connect the code to its actual behavior. |


## 9. AI Assistance Disclosure

I used AI as a learning and development support tool during this assignment. It helped me understand Kubernetes concepts, write and improve parts of the Python controller, troubleshoot technical problems, and organize my documentation.

I personally set up the Kubernetes environment, ran the program, performed the tests, and verified the results. AI provided guidance throughout the project, while I used practical testing to understand and confirm how the system worked.

![AI-assisted project badge](AIA-Artificial-Intelligence-Aided.png)



## 10. References

- [Kubernetes components](https://kubernetes.io/docs/concepts/overview/components/)
- [Kubernetes Pods and Pod lifecycle](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/)
- [Custom resources and CRDs](https://kubernetes.io/docs/concepts/extend-kubernetes/api-extension/custom-resources/)
- [Kubernetes controller pattern](https://kubernetes.io/docs/concepts/architecture/controller/)
- [Kubernetes Python client](https://github.com/kubernetes-client/python)
- [kind quick start](https://kind.sigs.k8s.io/docs/user/quick-start/)

---
