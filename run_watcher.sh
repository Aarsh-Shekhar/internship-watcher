#!/bin/zsh
# Runs one cycle of the internship watcher with a persistent ntfy topic

cd "/Users/aarshshekhar/Downloads/internship-watcher"
export NTFY_TOPIC="aarsh-internships"   # change the topic if you want
source .venv/bin/activate
python 03_watch_once.py
