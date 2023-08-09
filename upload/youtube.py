#!/usr/bin/python

"""
Most of the code is taken from https://developers.google.com/youtube/v3/guides/uploading_a_video
See the website for more details.
"""

import http.client as httplib
import httplib2
import os
import random
import sys
import time
import logging

from apiclient.discovery import build
from apiclient.errors import HttpError
from apiclient.http import MediaFileUpload
from oauth2client.client import flow_from_clientsecrets
from oauth2client.file import Storage
from oauth2client.tools import argparser, run_flow

logger = logging.getLogger("YoutubeUpload")

THIS_FILE = os.path.join(os.path.dirname(__file__))
CLIENT_SECRETS_FILE = os.path.join(
    THIS_FILE,
    os.pardir,
    "passwords",
    "youtube_secrets.json",
)

YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"

MISSING_CLIENT_SECRETS_MESSAGE = """
WARNING: Please configure OAuth 2.0

To make this sample run you will need to populate the client_secrets.json file
found at:

   {:s}

with information from the API Console
https://console.cloud.google.com/

For more information about the client_secrets.json file format, please visit:
https://developers.google.com/api-client-library/python/guide/aaa_client_secrets
""".format(CLIENT_SECRETS_FILE)

VALID_PRIVACY_STATUSES = ("public", "private", "unlisted")

httplib2.RETRIES = 1
RETRIABLE_STATUS_CODES = [500, 502, 503, 504]
# Always retry when these exceptions are raised.
RETRIABLE_EXCEPTIONS = (httplib2.HttpLib2Error, IOError, httplib.NotConnected,
  httplib.IncompleteRead, httplib.ImproperConnectionState,
  httplib.CannotSendRequest, httplib.CannotSendHeader,
  httplib.ResponseNotReady, httplib.BadStatusLine)

def upload_to_youtube(file_path, video_name):
    logger.debug("Uploading to YouTube: {}, {}".format(file_path, video_name))
    yt_service = get_authenticated_service()
    logger.debug("YouTube auth service loaded")

    video_id = None
    try:
        video_id = initialize_upload(
            yt_service,
            file_path,
            title=video_name,
            keywords=["SKV", "automatic"]
        )
    except HttpError as e:
        logger.debug("An HTTP error {:d} occurred:\n{:s}".format(e.resp.status, e.content))
    
    return video_id


def get_authenticated_service():
    flow = flow_from_clientsecrets(
        CLIENT_SECRETS_FILE,
        scope=YOUTUBE_UPLOAD_SCOPE,
        message=MISSING_CLIENT_SECRETS_MESSAGE,
    )
    storage = Storage(os.path.join(THIS_FILE, os.pardir, "passwords", "oauth2.json"))
    credentials = storage.get()

    if credentials is None or credentials.invalid:
        credentials = run_flow(flow, storage)

    return build(
        YOUTUBE_API_SERVICE_NAME,
        YOUTUBE_API_VERSION,
        http=credentials.authorize(httplib2.Http()),
    )


def initialize_upload(
        youtube,
        filepath,
        title,
        description="",
        privacyStatus="unlisted",
        category="17",
        keywords=[]
    ):
  
    tags = None
    if len(keywords) > 0:
        tags = keywords

    body=dict(
        snippet=dict(
        title=title,
        description=description,
        tags=tags,
        categoryId=category
        ),
        status=dict(
        privacyStatus=privacyStatus
        )
    )

    insert_request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=MediaFileUpload(filepath, chunksize=-1, resumable=True)
    )

    return resumable_upload(insert_request)


def resumable_upload(
        insert_request,
        max_retries=10
    ):
    response = None
    retry = 0

    video_id = None

    while response is None:
        retriable = False
        try:
            status, response = insert_request.next_chunk()
            logger.debug(status, response)
            if response is not None:
                if 'id' in response:
                    video_id = response['id']
                else:
                    logger.error("The upload failed with an unexpected response: {:s}".format(response))
                    return
        except HttpError as e:
            if e.resp.status in RETRIABLE_STATUS_CODES:
                error = "A retriable HTTP error %d occurred:\n%s" % (e.resp.status,
                                                                    e.content)
            else:
                raise
        except RETRIABLE_EXCEPTIONS as e:
            retriable = True
            logger.debug("Caught retriable exception.")
        except Exception as e:
            logger.debug("Error occurred during YouTube upload.")
            raise e

        if retriable:
            retry += 1
            if retry > max_retries:
                logger.error("YouTube upload failed after {:d} tries. No longer trying.".format(retry))
                return

        if response is None:
            max_sleep = 2 ** retry
            sleep_seconds = random.random() * max_sleep
            logger.debug("Sleeping {:f} seconds and then retrying...".format(sleep_seconds))
            time.sleep(sleep_seconds)

    return video_id