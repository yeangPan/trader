#!/usr/bin/env python
# -*- encoding: utf-8 -*-
'''
@File    : loading.py
@Time    : 2024/12/25 15:04:11
@Author  : yangp
@Contact : yeangpan@outlook.com
@Version : 0.1
@Copyright (c) 2024 Yang Pan
@Licensed under the Apache License, Version 2.0.
@Desc : None
'''


import sys
import os
import django
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Add the project root and dashboard directory to Python path
sys.path.append(str(PROJECT_ROOT))
sys.path.append(str(PROJECT_ROOT.parent / "dashboard"))

# Configure Django settings
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dashboard.settings")
os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

# Initialize Django
django.setup()
