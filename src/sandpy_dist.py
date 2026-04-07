import os
import httpx
import cloudpickle
import base64

DISPATCHER_URL = os.environ.get("DISPATCHER_URL", "http://job-farm-dispatcher:8000")

def map(func, iterable):
    """
    Distributes `func` over `iterable` across multiple nodes in the SandPy cluster.
    Returns: A list of results, matching the order of the iterable.
    """
    try:
        # Serialize the function using cloudpickle so we can send it over the wire
        func_bytes = cloudpickle.dumps(func)
        func_b64 = base64.b64encode(func_bytes).decode('utf-8')
    except Exception as e:
        raise ValueError(f"Function could not be serialized for MapReduce: {e}")

    payload = {
        "func_b64": func_b64,
        "iterable": list(iterable)
    }

    try:
        # Send to the dispatcher, which will orchestrate and chunk the workload
        with httpx.Client(timeout=300.0) as client:
            resp = client.post(f"{DISPATCHER_URL}/api/map", json=payload)
            if resp.status_code == 200:
                data = resp.json()
                if "results" in data:
                    return data["results"]
                else:
                    raise RuntimeError(f"MapReduce failed: {data}")
            else:
                raise RuntimeError(f"Dispatcher returned {resp.status_code}: {resp.text}")
    except Exception as e:
        raise RuntimeError(f"MapReduce failed to communicate with Dispatcher: {e}")
