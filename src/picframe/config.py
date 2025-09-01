#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Global configuration settings for picframe.
"""

# Global debug variable to switch between WAL and DELETE journal modes
# Set to 'WAL' for better concurrency, 'DELETE' for DB Browser compatibility
DB_JRNL_MODE = "WAL"
