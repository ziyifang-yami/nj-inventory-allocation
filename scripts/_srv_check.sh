#!/bin/bash
find /opt /root /home /srv /app /var/www -name 'athena_cache_ts.txt' 2>/dev/null
echo "---csv---"
find /opt /root /home /srv /app /var/www -name 'athena_cache.csv' 2>/dev/null | head -5
