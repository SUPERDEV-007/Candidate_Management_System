import subprocess
with open('pytest_out.txt', 'w', encoding='utf-8') as f:
    subprocess.run(['pytest', 'test_api.py', '-v'], stdout=f, stderr=subprocess.STDOUT)
