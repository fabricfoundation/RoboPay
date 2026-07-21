import sys
import time

try:
    import zenoh
except ImportError:
    print("[ERROR] Zenoh module not found. Run 'python -m pip install eclipse-zenoh'")
    sys.exit(1)

def main():
    print("==========================================")
    print("  RoboPay Zenoh Simulation Bridge v1.0  ")
    print("==========================================")
    
    # Configure and open Zenoh Session
    conf = zenoh.Config()
    print("[Zenoh] Opening session...")
    session = zenoh.open(conf)
    
    # Declare publisher
    pub_topic = "robopay/telemetry/status"
    publisher = session.declare_publisher(pub_topic)
    print(f"[Zenoh] Publishing on topic: '{pub_topic}'")
    
    # Heartbeat loop simulation
    counter = 0
    try:
        while True:
            payload = f"STATUS: ACTIVE | CYCLE: {counter} | PROTOCOL: v25"
            publisher.put(payload)
            print(f"[TX] {payload}")
            counter += 1
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[Zenoh] Closing session...")
        session.close()

if __name__ == "__main__":
    main()