"""This function kicks off a code build job"""
from __future__ import print_function
import http.client
from urllib.parse import urlparse
import json
import boto3
import logging


def lambda_handler(event, context):
    """Main Lambda Handling function"""

    def log_config(loglevel=None, botolevel=None):
        if 'ResourceProperties' in event.keys():
            if 'loglevel' in event['ResourceProperties'] and not loglevel:
                loglevel = event['ResourceProperties']['loglevel']
            if 'botolevel' in event['ResourceProperties'] and not botolevel:
                botolevel = event['ResourceProperties']['botolevel']
        if not loglevel:
            loglevel = 'warning'
        if not botolevel:
            botolevel = 'error'

        # Set log verbosity levels
        loglevel = getattr(logging, loglevel.upper(), 20)
        botolevel = getattr(logging, botolevel.upper(), 40)
        mainlogger = logging.getLogger()
        mainlogger.setLevel(loglevel)
        logging.getLogger('boto3').setLevel(botolevel)
        logging.getLogger('botocore').setLevel(botolevel)

        mylogger = logging.getLogger("lambda_handler")
        mylogger.setLevel(loglevel)

        return logging.LoggerAdapter(
            mylogger,
            {'requestid': event.get('RequestId','__None__')}
        )

    def cleanup_images():
        """loop over and delete images in each repo"""

        properties = event['ResourceProperties']
        for repository in [
            'AWXTaskRegistry',
            'AWXWebRegistry',
            'MemcachedRegistry',
            'RabbitMQRegistry',
            'SidecarRegistry'
        ]:
            logger.debug("Cleaning Up: " + repository)
            logger.debug("Trying to cleanup: " + properties[repository])
            cleanup_images_repo(properties[repository])

    def cleanup_images_repo(repository):
        """Delete Container images"""
        logger.debug(account_id)
        ecr_client = boto3.client('ecr')
        response = ecr_client.describe_images(
            registryId=account_id,
            repositoryName=repository
        )

        imageIds = []
        for imageDetail in response['imageDetails']:
            imageIds.append(
                {
                    'imageDigest': imageDetail['imageDigest'],
                }
            )

        if len(imageIds):
            logger.debug("Deleting images")
            response = ecr_client.batch_delete_image(
                registryId=account_id,
                repositoryName=repository,
                imageIds=imageIds
            )

    def execute_build():
        """Kickoff CodeBuild Project"""
        build = boto3.client('codebuild')

        project_name = event["ResourceProperties"]["BuildProjectName"]
        signal_url = event["ResponseURL"]
        stack_id = event["StackId"]
        request_id = event["RequestId"]
        logical_resource_id = event["LogicalResourceId"]
        url = urlparse(event['ResponseURL'])

        logger.info("Kicking off build: {}".format(project_name))

        environment = [
            {'name': 'url_path',                'value': url.path},
            {'name': 'url_query',               'value': url.query},
            {'name': 'cfn_signal_url',          'value': signal_url},
            {'name': 'cfn_stack_id',            'value': stack_id},
            {'name': 'cfn_request_id',          'value': request_id},
            {'name': 'cfn_logical_resource_id', 'value': logical_resource_id}
        ]

        response = build.start_build(
            projectName=project_name,
            environmentVariablesOverride=environment
        )
        return response

    def get_response_dict():
        """Setup Response object for CFN Signal"""

        response_dict = {
            'StackId': event['StackId'],
            'RequestId': event['RequestId'],
            'LogicalResourceId': event['LogicalResourceId'],
            'Status': 'SUCCESS'
        }

        return response_dict

    def send_response(status=None, reason=None):
        if status is not None:
            response['Status'] = status

        if reason is not None:
            response['Reason'] = reason

        if 'ResponseURL' in event and event['ResponseURL']:
            url = urlparse(event['ResponseURL'])
            logger.debug(url.hostname)
            body = json.dumps(response)
            https = http.client.HTTPSConnection(url.hostname)
            https.request('PUT', url.path + '?' + url.query, body)
            logger.info("Sent CFN Response")

        return response

    """Main Lambda Logic"""
    logger = log_config()
    logger.info(event)

    # Setup base response
    response = get_response_dict()
    account_id = context.invoked_function_arn.split(":")[4]
    response['PhysicalResourceId'] = "1233244324"

    # CREATE UPDATE (want to avoid rebuilds unless something changed)
    if event['RequestType'] in ("Create", "Update"):
        try:
            execute_build()
        except Exception as exce:
            logger.error("Build threw exception: " + str(exce))
            logger.error(exce, exc_info=True)
            # Signal back that we failed
            return send_response(
                status="FAILED",
                reason=str(exce)
            )
        else:  # We want codebuild to send the signal
            logger.info("Codebuild project running, Codebuild will signal back")
            return
    elif event['RequestType'] == "Delete":
        try:
            logger.info("Cleaning up repositories and images")

            # Cleanup the images in the repository
            cleanup_images()
        except Exception as exce:
            logger.error("Exception: " + str(exce))
            logger.error(exce, exc_info=True)

            # Signal back that we failed
            return send_response(
                status="FAILED",
                reason=str(exce)
            )

        # signal success to CFN
        logger.info("Cleanup complete signal back")
        return send_response()
    else:  # Invalid RequestType
        message = "Invalid request type send error signal to cfn: {} " \
                  "(expecting: Create, Update, Delete)" \
                  "".format(event['RequestType'])

        logger.error(message)
        return send_response(
            status="FAILED",
            reason=message
        )
