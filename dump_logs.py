import subprocess

try:
    output = subprocess.check_output(["docker-compose", "logs", "--tail=50", "worker1"], stderr=subprocess.STDOUT)
    print(output.decode())
except Exception as e:
    print(e)
