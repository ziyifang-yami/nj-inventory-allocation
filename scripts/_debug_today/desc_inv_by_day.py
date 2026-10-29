"""Describe wh_inventory_transaction_by_day in Athena."""
import sys, os
sys.path.insert(0, 'c:/Project-Z/NJ_Inventory_Allocation')
from data.loader import _load_env
_load_env()

import boto3, time, configparser

# Try IAM key first, then profile
aws_key = os.getenv('AWS_ACCESS_KEY_ID')
if aws_key:
    session = boto3.Session(
        aws_access_key_id=aws_key,
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
        region_name='us-west-2'
    )
else:
    cfg = configparser.ConfigParser()
    cfg.read(os.path.expanduser('~/.aws/config'))
    profiles = [s.replace('profile ', '') for s in cfg.sections()]
    profile = next((p for p in ['athena-prod', 'prod', 'default'] if p in profiles), 'default')
    print(f'Using AWS profile: {profile}')
    session = boto3.Session(profile_name=profile, region_name='us-west-2')

athena = session.client('athena')
WG = os.getenv('ATHENA_WORK_GROUP', 'aiagent')
S3 = (os.getenv('ATHENA_S3_STAGING') or 's3://yami-athena-query-result/') + 'debug/'

def run(sql, db='yamibuy_wh'):
    resp = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={'Database': db},
        WorkGroup=WG,
        ResultConfiguration={'OutputLocation': S3}
    )
    qid = resp['QueryExecutionId']
    print(f'  Query ID: {qid}')
    for _ in range(60):
        st = athena.get_query_execution(QueryExecutionId=qid)['QueryExecution']['Status']['State']
        if st == 'SUCCEEDED': break
        if st in ('FAILED', 'CANCELLED'):
            reason = athena.get_query_execution(QueryExecutionId=qid)['QueryExecution']['Status'].get('StateChangeReason', '')
            return None, f'{st}: {reason}'
        time.sleep(2)
    rows = []
    for page in athena.get_paginator('get_query_results').paginate(QueryExecutionId=qid):
        rows.extend(page['ResultSet']['Rows'])
    return rows, None

print('=== DESCRIBE yamibuy_wh.wh_inventory_transaction_by_day ===')
rows, err = run('DESCRIBE yamibuy_wh.wh_inventory_transaction_by_day')
if err:
    print('ERROR:', err)
else:
    for r in rows:
        print('  ' + ' | '.join(c.get('VarCharValue', '') for c in r['Data']))

print()
print('=== Sample 3 rows for item 5021184261 ===')
rows2, err2 = run("""
    SELECT *
    FROM yamibuy_wh.wh_inventory_transaction_by_day
    WHERE item_number = '5021184261'
      AND warehouse_number = '002'
    LIMIT 3
""")
if err2:
    print('ERROR:', err2)
else:
    for r in rows2:
        print('  ' + ' | '.join(c.get('VarCharValue', '') for c in r['Data']))
