import subprocess
import sys

if __name__ == "__main__":
    print("Starting unybrands Custom SaaS Cockpit on http://localhost:8000 ...")
    try:
        subprocess.run([sys.executable, "server.py"], check=True)
    except KeyboardInterrupt:
        print("Shutting down server.")
    except Exception as e:
        print(f"Error starting server: {e}")
