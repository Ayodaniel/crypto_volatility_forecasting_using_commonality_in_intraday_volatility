#!/bin/bash
clear
source ../venv/bin/activate
#######################################################
## Model performance ClustAM.
#######################################################
for l in {6M,1M,1W}
  do
    for regression_type in {lightgbm,elastic,lasso,pcr}
      do
        for model in {har_eq,har,ar}
          do
            python3 ../generate_results.py --L=$l --model=$model --training_scheme=ClustAM \
            --regression=$regression_type --transformation=log &
          done
          wait
      done
  done
deactivate
