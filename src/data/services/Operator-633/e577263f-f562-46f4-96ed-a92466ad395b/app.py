import numpy as np
from flask import Flask, request, jsonify

app = Flask(__name__)

def add(x, y):
    return x + y

def subtract(x, y):
    return x - y

def multiply(x, y):
    return x * y

def divide(x, y):
    if y == 0:
        return "Error: Division by zero is not allowed"
    else:
        return x / y

@app.route('/calculate', methods=['POST'])
def calculate():
    try:
        data = request.get_json()
        operation = data['operation']
        num1 = float(data['num1'])
        num2 = float(data['num2'])

        if operation == 'add':
            result = add(num1, num2)
        elif operation == 'subtract':
            result = subtract(num1, num2)
        elif operation == 'multiply':
            result = multiply(num1, num2)
        elif operation == 'divide':
            result = divide(num1, num2)
        else:
            result = "Error: Invalid operation"

        return jsonify({'result': result})
    except KeyError as e:
        return jsonify({'result': f"Error: Missing key {e}"}), 400
    except ValueError as e:
        return jsonify({'result': f"Error: Invalid value {e}"}), 400
    except Exception as e:
        return jsonify({'result': str(e)}), 500

if __name__ == '__main__':
    print("Calculator server started on port 8000")
    app.run(host='0.0.0.0', port=8000, use_reloader=False, threaded=True)