#!/bin/bash
clear
source ../venv/bin/activate
python3 ../generate_eda.py --save=1
deactivate