import boto3
from botocore.exceptions import ClientError
import os

# Create a dummy CSV file
with open('test.csv', 'w') as f:
    f.write('name,age,city\n')
    f.write('Alice,30,New York\n')
    f.write('Bob,25,Chicago\n')

print("✓ test.csv created locally")

# Upload to S3
s3 = boto3.client('s3', region_name='us-east-2')

try:
    s3.upload_file('test.csv', 'dataprocessor-input-bucket', 'test.csv')
    print("✓ Uploaded successfully to S3!")
except ClientError as e:
    print(f"✗ Upload failed: {e}")

# Cleanup local file
os.remove('test.csv')
print("✓ Local test.csv removed")