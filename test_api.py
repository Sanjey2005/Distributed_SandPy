import requests
import json

def execute_code(user_id, code):
    # replace with the host and port applicable to your docker container
    url = "http://localhost:5002/execute"
    payload = {
        "user_id": user_id,
        "code": code
    }
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()  # Raise exception for non-200 status codes
        return response.json()
    except requests.RequestException as e:
        print(f"Execute code error: {str(e)}")
        if hasattr(e.response, 'json'):
            print(f"Server response: {e.response.json()}")
        return None

def start_session(user_id):
    try:
        response = requests.post(
          # replace with the host and port applicable to your docker container
           "http://localhost:5002/start_session",
            data={"user_id": user_id}
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"Start session error: {str(e)}")
        if hasattr(e.response, 'json'):
            print(f"Server response: {e.response.json()}")
        return None

# Test sequence
user_id = "user_test"

# Start session
session_result = start_session(user_id)
if session_result:
    print(f"Session started: {session_result}")

    # Test with simple code first
    print("\nTesting simple print...")
    result = execute_code(user_id, 'print("Simple test")')
    print(f"Result: {result}")

    # Test with your function
    print("\nTesting function definition and call...")
    code = '''
def greet(name):
    print(f"Hello, {name}!")
    
greet("World")
'''

    test_code = 'import pandas as pd\n\n# Load the Titanic dataset\nfile_path = "/mnt/data/titanic.csv"\ndata = pd.read_csv(file_path)\nprint("Data loaded successfully.") # Process the data to calculate survival counts by sex\nsurvival_count = data.groupby(["Sex", "Survived"]).size().unstack(fill_value=0)\nprint("Survival counts calculated:{survival_count}")\nimport matplotlib.pyplot as plt\n\n# Create a plot of survival counts by sex\nsurvival_count.plot(kind="bar", stacked=True)\nplt.title("Survived vs Dead by Sex")\nplt.xlabel("Sex")\nplt.ylabel("Number of Passengers")\nplt.xticks(rotation=0)\nplt.legend(["Did not survive", "Survived"])\nplt.tight_layout() # Save the plot as an image file\noutput_file_path = "/mnt/data/output/output.png"\nplt.savefig(output_file_path)\n, # Print output locations and data\n\nprint(survival_count)\nprint(f"Plot saved at: {output_file_path}")'
    result = execute_code(user_id, code)
    print(f"Result: {result}")

    # Test variable persistence
    print("\nTesting variable persistence...")
    result = execute_code(user_id, 'x = 42')
    print(f"Set variable result: {result}")
    result = execute_code(user_id, 'print(f"The value of x is {x}")')
    print(f"Get variable result: {result}")
    result = execute_code(user_id, test_code)
    print(f"Get test code result: {result}")



"""
python3 test_api.py
## expected output
Session started: {'message': 'Session started successfully', 'notebook_path': '/mnt/jupyter_sessions/user_test/notebook_user_test.ipynb'}
Testing simple print...
Result: {'output': ''}
Testing function definition and call...
Result: {'output': 'Hello, World!\n'}
Testing variable persistence...
Set variable result: {'output': ''}
Get variable result: {'output': 'The value of x is 42\n'}
"""
