import logging
import json
from boto3 import Session
from botocore.exceptions import ClientError

# Setup Logging
logger = logging.getLogger('s3')
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
logger.addHandler(handler)

class S3:
    def __init__(self, access_key, secret_key, bucket):
        self.bucket = bucket
        session = Session(aws_access_key_id=access_key, aws_secret_access_key=secret_key)
        self.s3_client = session.client('s3')

    def put_object(self, key, data):
        try:
            logger.info("Storing current running state as persistent state in S3")
            json_data = json.dumps(data)
            self.s3_client.put_object(Bucket=self.bucket, Key=key, Body=json_data)
        except Exception as error:
            raise error

    def get_object(self, key):
        try:
            logger.info("Retrieving persistent state from S3")
            data = self.s3_client.get_object(Bucket=self.bucket,Key=key)
            content = data["Body"].read().decode("utf-8")
            return json.loads(content)
        except ClientError as error:
            error_code = error.response["Error"]["Code"]
            # Assume we have a new key
            # If the key isn't there we get AccessDenied instead of NoSuchKey for some reason
            if error_code == "NoSuchKey" or error_code == "AccessDenied":
                logger.warn("Existing json state not found in S3, assuming first run and returning new state")
                content = {}
                return content
            else:
                raise error
