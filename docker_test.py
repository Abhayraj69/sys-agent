import docker

def run_secure_sandbox_test():
    print("Initializing Docker client...")
    try:
        # Connects to your local Docker daemon
        client = docker.from_env()
    except docker.errors.DockerException as e:
        print(f"❌ Failed to connect to Docker. Is Docker Desktop/Daemon running?\nError: {e}")
        return

    print("✅ Connected to Docker. Spinning up sandbox...")

    try:
        # Run a simple Python command inside a temporary alpine container
        # We use python:3.9-alpine as it is incredibly small and fast to download/start
        container_output = client.containers.run(
            image="python:3.9-alpine",
            command='python -c "print(\'Hello from inside the secure sandbox!\')"',
            remove=True,               # The container deletes itself immediately after running
            network_mode="none",       # No internet access inside the container
            mem_limit="128m",          # Hard cap on RAM usage
            stdout=True,
            stderr=True
        )
        
        # Decode the byte output to a string
        output_str = container_output.decode('utf-8').strip()
        print("\n--- Sandbox Output ---")
        print(f"🚀 {output_str}")
        print("----------------------\n")
        print("✅ Success! The bridge to Docker is fully operational.")

    except docker.errors.ImageNotFound:
        print("❌ Image not found. Run 'docker pull python:3.9-alpine' in your terminal first.")
    except Exception as e:
        print(f"❌ An error occurred during execution:\n{e}")

if __name__ == "__main__":
    run_secure_sandbox_test()