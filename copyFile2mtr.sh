#! /bin/bash

scp -r automated_survey_flask/templates/*.html  mtrapp@188.166.110.236:/home/mtrapp/appl/automated-survey-flask/automated_survey_flask/templates/
scp -r automated_survey_flask/* mtrapp@188.166.110.236:/home/mtrapp/appl/automated-survey-flask/automated_survey_flask/
scp -r *.py  mtrapp@188.166.110.236:/home/mtrapp/appl/automated-survey-flask/

