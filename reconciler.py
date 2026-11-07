
# COSC 3657 - Distributed Systems - Unit 2
# Project: Mini-Kubernetes Reconciliation Controller
# Purpose: Automatically keep the correct number of Pods running.


# Import threading so we can watch multiple Kubernetes events.
import threading

# Import time so the program can pause before retrying.
import time

# Import the tools needed to communicate with Kubernetes.
from kubernetes import client, config, watch

# Import this to handle errors returned by Kubernetes.
from kubernetes.client.rest import ApiException


# These settings must match the professor's YAML files.

# The API group defined in crd_definition.yaml.
GROUP = "cosc3657.tu.ca"

# The version of our custom Kubernetes resource.
VERSION = "v1alpha1"

# The plural name used to find our custom resources.
PLURAL = "resilientdeployments"

# The Kubernetes namespace where our Pods will run.
NAMESPACE = "default"

# This label helps identify Pods managed by our controller.
POD_LABEL = "app.kubernetes.io/managed-by"


# Create an event to notify the controller when something changes.
change_detected = threading.Event()

# Remember previous Pod counts to avoid repeating log messages.
last_state = {}


# This function creates one new Kubernetes Pod.
def create_pod(core_api, resource, image):

    # Get the application name from the custom resource.
    name = resource["metadata"]["name"]

    # Get its unique ID to identify the owner of the Pod.
    uid = resource["metadata"]["uid"]

    # The HTTP echo application needs a message when it starts.
    # Other images will use their normal startup settings.
    if image.split(":")[0] == "hashicorp/http-echo":
        args = ["-text=Hello from COSC 3657"]
    else:
        args = None

    # Define the new Pod and its settings.
    pod = client.V1Pod(

        # Metadata stores the Pod's name, labels, and owner.
        metadata=client.V1ObjectMeta(

            # Kubernetes will generate a unique Pod name.
            generate_name=f"{name}-",

            # Add a label so we can find this Pod later.
            labels={POD_LABEL: name},

            # Record which custom resource owns this Pod.
            owner_references=[
                client.V1OwnerReference(

                    # Identify the owner's API version.
                    api_version=f"{GROUP}/{VERSION}",

                    # Identify the owner's resource type.
                    kind="ResilientDeployment",

                    # Store the owner's name.
                    name=name,

                    # Store the owner's unique ID.
                    uid=uid,

                    # Mark our resource as the Pod's controller.
                    controller=True,
                )
            ],
        ),

        # Define what will run inside the Pod.
        spec=client.V1PodSpec(

            # Every Pod needs at least one container.
            containers=[
                client.V1Container(

                    # Give the container a name.
                    name="application",

                    # Use the image requested in sample_cr.yaml.
                    image=image,

                    # Pass startup arguments when needed.
                    args=args,
                )
            ],

            # Restart the container if it crashes.
            restart_policy="Always",
        ),
    )

    # Ask Kubernetes to create the new Pod.
    created = core_api.create_namespaced_pod(
        namespace=NAMESPACE,
        body=pod,
    )

    # Show the name of the Pod we just created.
    print(f"[CREATE] {created.metadata.name}", flush=True)


# This function checks the number of Pods and fixes differences.
def reconcile_all(core_api, custom_api):

    # Get all ResilientDeployment resources in our namespace.
    resources = custom_api.list_namespaced_custom_object(
        group=GROUP,
        version=VERSION,
        namespace=NAMESPACE,
        plural=PLURAL,
    )

    # Check each custom resource one at a time.
    for resource in resources.get("items", []):

        # Read the application's name.
        name = resource["metadata"]["name"]

        # Read how many Pods the user wants.
        desired = resource["spec"]["replicas"]

        # Read which container image should be used.
        image = resource["spec"]["image"]

        # Try to check and manage the application's Pods.
        try:

            # Find Pods with the label belonging to this application.
            response = core_api.list_namespaced_pod(
                namespace=NAMESPACE,
                label_selector=f"{POD_LABEL}={name}",
            )

            # Keep only Pods that are still active.
            active_pods = [
                pod for pod in response.items

                # Ignore Pods that Kubernetes is deleting.
                if pod.metadata.deletion_timestamp is None

                # Ignore Pods that have permanently finished.
                and pod.status.phase not in ("Failed", "Succeeded")
            ]

            # Count the active Pods, including Pods still starting.
            observed = len(active_pods)

            # Calculate how many Pods are missing or extra.
            drift = desired - observed

            # Check whether the counts changed since the last check.
            state_changed = (
                last_state.get(name) != (desired, observed)
            )

            # Print the new counts when something changes.
            if state_changed:

                print(
                    f"[RECONCILE LOOP] Resource: {name} | "
                    f"Desired: {desired} | "
                    f"Observed: {observed} | "
                    f"Drift: {drift}",
                    flush=True,
                )

                # Save these counts for the next comparison.
                last_state[name] = (desired, observed)

            # If drift is positive, we need more Pods.
            if drift > 0:

                # Show how many Pods must be created.
                print(
                    f"Scale-Up Event: Creating {drift} Pod(s)...",
                    flush=True,
                )

                # Create one Pod for each missing replica.
                for _ in range(drift):

                    # Call our function to create a Pod.
                    create_pod(core_api, resource, image)

            # If drift is negative, we have too many Pods.
            elif drift < 0:

                # Show how many Pods must be removed.
                print(
                    f"Scale-Down Event: Removing {-drift} Pod(s)...",
                    flush=True,
                )

                # Sort the Pods, putting the newest ones first.
                newest_first = sorted(
                    active_pods,
                    key=lambda pod: pod.metadata.creation_timestamp,
                    reverse=True,
                )

                # Select only the extra Pods.
                for pod in newest_first[:-drift]:

                    # Ask Kubernetes to delete this Pod.
                    core_api.delete_namespaced_pod(
                        name=pod.metadata.name,
                        namespace=NAMESPACE,
                    )

                    # Show which Pod is being deleted.
                    print(
                        f"[DELETE] {pod.metadata.name}",
                        flush=True,
                    )

            # If drift is zero, the Pod count is correct.
            else:

                # Find Pods using an image different from the request.
                outdated = [
                    pod for pod in active_pods
                    if pod.spec.containers[0].image != image
                ]

                # Replace Pods that use an outdated image.
                if outdated:

                    print(
                        "[UPDATE] Replacing Pods with the wrong image...",
                        flush=True,
                    )

                    # Delete outdated Pods so replacements can be made.
                    for pod in outdated:

                        core_api.delete_namespaced_pod(
                            name=pod.metadata.name,
                            namespace=NAMESPACE,
                        )

                # If everything matches, no changes are needed.
                elif state_changed:

                    print(
                        f"EQUILIBRIUM REACHED for {name}.",
                        flush=True,
                    )

        # Handle Kubernetes errors without stopping the controller.
        except ApiException as error:

            # Display the error so we know what went wrong.
            print(
                f"[ERROR] Could not reconcile {name}: {error}",
                flush=True,
            )


# This function watches Kubernetes for changes.
def watch_for_changes(api_function, **options):

    # Keep watching while the controller is running.
    while True:

        # Try to connect to the Kubernetes event stream.
        try:

            # Create an object that watches Kubernetes events.
            watcher = watch.Watch()

            # Listen for changes using the requested API function.
            for event in watcher.stream(
                api_function,
                timeout_seconds=30,
                **options,
            ):

                # Check whether something was added, changed, or deleted.
                if event["type"] in ("ADDED", "MODIFIED", "DELETED"):

                    # Notify the controller to check the Pods again.
                    change_detected.set()

        # Handle connection errors while watching.
        except Exception as error:

            # Print the problem instead of crashing the program.
            print(f"[WATCH ERROR] Retrying: {error}", flush=True)

            # Pause for two seconds before trying again.
            time.sleep(2)


# This is the main function that starts our controller.
def main():

    # Try to load the local Kubernetes connection settings.
    try:

        # Read the kubeconfig file to connect to our cluster.
        config.load_kube_config()

    # Handle a missing or invalid Kubernetes configuration.
    except Exception as error:

        # Explain why the connection failed.
        print(f"Cannot connect to Kubernetes yet: {error}")

        # Stop because Kubernetes is not available.
        return

    # Create an API client to manage normal Kubernetes Pods.
    core_api = client.CoreV1Api()

    # Create an API client to manage our custom resources.
    custom_api = client.CustomObjectsApi()

    # Start a separate thread to watch normal Pod changes.
    threading.Thread(

        # Run our watch function.
        target=watch_for_changes,

        # Tell the function which Kubernetes API to watch.
        args=(core_api.list_namespaced_pod,),

        # Watch Pods inside the default namespace.
        kwargs={"namespace": NAMESPACE},

        # Let this thread stop when the main program ends.
        daemon=True,

    ).start()

    # Start another thread to watch ResilientDeployment changes.
    threading.Thread(

        # Run the same watch function for custom resources.
        target=watch_for_changes,

        # Use the custom resource API this time.
        args=(custom_api.list_namespaced_custom_object,),

        # Tell Kubernetes which custom resources to watch.
        kwargs={
            "group": GROUP,
            "version": VERSION,
            "namespace": NAMESPACE,
            "plural": PLURAL,
        },

        # Let this thread stop when the main program ends.
        daemon=True,

    ).start()

    # Show a message when our controller starts.
    print(
        "=== COSC 3657 Custom Kubernetes Reconciler Started ===",
        flush=True,
    )

    # Keep the main controller running.
    try:

        # Repeat the reconciliation process continuously.
        while True:

            # Try checking and fixing the Pods.
            try:

                # Compare actual Pods with requested Pods.
                reconcile_all(core_api, custom_api)

            # Handle errors returned by Kubernetes.
            except ApiException as error:

                # Show the error and continue running.
                print(f"[API ERROR] Retrying: {error}", flush=True)

            # Wait for an event, or check again after one second.
            change_detected.wait(timeout=1)

            # Clear the notification before the next check.
            change_detected.clear()

    # Let the user stop the controller using Ctrl+C.
    except KeyboardInterrupt:

        # Show that the controller has stopped.
        print("\nController stopped.")


# Check whether this Python file was started directly.
if __name__ == "__main__":

    # Start the main controller function.
    main()
