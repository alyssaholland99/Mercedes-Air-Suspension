sudo apt update
sudo apt install -y python3-full swig python3-dev liblgpio-dev gcc

python3 -m venv /suspension/venv
source /suspension/venv/bin/activate

pip3 install adafruit-circuitpython-ads1x15 flask gpiozero