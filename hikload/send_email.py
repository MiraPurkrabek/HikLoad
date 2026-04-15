import os
import yaml
import logging
import logging.config
from datetime import datetime

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from hikload.app_logging import get_latest_log_path

logger = logging.getLogger('EmailSender')

developer_email = "miroslav.purkrabek@skvflorbal.cz"

def _load_credentials():
    try:
        this_file_path = os.path.dirname(os.path.abspath(__file__))
        passwords_path = os.path.join(this_file_path, "..", "passwords", "email.yml")
        with open(passwords_path) as pass_file:
            email_passwords = yaml.safe_load(pass_file)
    except FileNotFoundError:
        email_passwords = None
    logger.debug("Passwords loaded")
    
    return email_passwords

def send_email(
    to,
    cc=None,
    subject="Test email from HikLoad",
    body="This is a test email sent automatically through Python",
    bcc=None, 
):
    # Set up the email details
    msg = MIMEMultipart()
    msg['Subject'] = subject
    msg['To'] = to
    if cc is not None:
        msg['Cc'] = cc
    else:
        cc=""
    if bcc is not None:
        msg['Bcc'] = bcc
    else:
        bcc=""

    # Add intro
    body = "Hello Human,\n\n" + body

    # Add signature
    body += "\n\n"
    body += "Best regards,\nYour SKV Robot"
    body += "\n(please, do not respond to this email, I cannot read)"

    # Add the email body
    msg.attach(MIMEText(body, 'plain'))
    
    passwords = _load_credentials()
    if passwords is None:
        logger.error("No passwords for email sending. Email not sent.")
        return

    username = "{:s}@{:s}".format(passwords['o365']['username'], passwords['o365']['server'])
    msg['From'] = username
    
    # Set up the SMTP server and authenticate
    smtp_server = "smtp.office365.com"
    smtp_port = 587  # Use port 587 for STARTTLS
    
    try:
        # Connect to the SMTP server
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()  # Upgrade the connection to a secure encrypted SSL/TLS connection
            server.login(username, passwords['o365']['password'])  # Authenticate with the server
            receivers = [address.strip() for address in (cc.split(",") + bcc.split(",") + to.split(",")) if address.strip()]
            server.sendmail(msg['From'], receivers, msg.as_string())  # Send the email
            logger.info("Email sent successfully!")
    except Exception as e:
        logger.warning("Failed to send email: {}".format(e))
        
    return


def send_report_email(to=None, log_path=None, role=None):
    if log_path is None:
        if role is not None:
            log_path = get_latest_log_path(role)
        else:
            this_file_path = os.path.dirname(os.path.abspath(__file__))
            log_path = os.path.join(this_file_path, "..", "logs", "latest.log")
    log_str = ""
    try:
        with open(log_path, "r") as f:
            log_str = f.read()
    except FileNotFoundError:
        log_str = "Crash report requested, but log file '{}' was not found.".format(log_path)
        
    if to is None:
        to = developer_email

    send_email(
        to=to,
        subject="[HikLoad] Crash report",
        body=log_str,
        bcc=developer_email
    )
    
def send_failure_email(to, body=None, video_name=None):
    video_label = video_name or "requested video"
    
    if body is None:
        body = "SKV server crashed while downloading your video '{:s}'. Try to download it again. If the problem persists (you see this email for the second time), contact Mira Purkrabek about details.".format(video_label)
    
    send_email(
        to = to,
        bcc = developer_email,
        body = body,
        subject="[SKV Video Server] Video download failed"
    )
    
def send_success_email(to, body=None, video_name=None):
    video_label = video_name or "requested video"
    
    if body is None:
        body = "Your video '{:s}' is ready. \n\n".format(video_label)
        body += "Download it at: "
        body += "https://sokolvinohrady-my.sharepoint.com/:f:/g/personal/robot_skv_01_skvflorbal_cz/Emm_27OYqjpFjM0jF9sFQSkBDubvdEIq1TJKbAoNjgN8cA?e=ZWS2EV"
        body += "\n\nThe video will be available for 7 days. After that it will be automatically deleted."
    
    send_email(
        to = to,
        bcc = developer_email,
        body = body,
        subject="[SKV Video Server] Video ready"
    )
    
def send_no_recordings_email(to, body=None, video_name=None):
    video_label = video_name or "requested video"
    if body is None:
        body = "You attempted to download video '{:s}' but no recordings were found. \n".format(video_label)
        body += "All videos are deleted after 30 days. Did you try to download older video? Or maybe end time is earlier than start time? If not, please contact Mira."
    
    send_email(
        to = to,
        bcc = developer_email,
        body = body,
        subject="[SKV Video Server] Video not found"
    )


def send_parse_failure_email(to, body=None):
    if body is None:
        body = "Your command was not parsed, probably due to some unexpected characters or invalid format. Please try again. If the problem persists, contact support."

    send_email(
        to=to,
        bcc=developer_email,
        body=body,
        subject="[SKV Video Server] Command parsing failed"
    )


def _format_registered_request_summary(video_name=None, command=None, harddisk_save=False):
    command = command or {}
    lines = []
    video_label = video_name or command.get("videoname") or "requested video"
    lines.append("Below is the parsed request:")
    lines.append("")
    lines.append("Video name: {}".format(video_label))

    start_value = command.get("starttime")
    end_value = command.get("endtime")
    if start_value and end_value:
        try:
            start_dt = datetime.fromisoformat(start_value)
            end_dt = datetime.fromisoformat(end_value)
            if start_dt.date() == end_dt.date():
                lines.append("Date: {}".format(start_dt.date().isoformat()))
                lines.append(
                    "Time: {} - {}".format(
                        "{}:{:02d}".format(start_dt.hour, start_dt.minute),
                        "{}:{:02d}".format(end_dt.hour, end_dt.minute),
                    )
                )
            else:
                lines.append("From: {}".format(start_dt.isoformat(sep=" ", timespec="minutes")))
                lines.append("To: {}".format(end_dt.isoformat(sep=" ", timespec="minutes")))
        except Exception:
            lines.append("Start time: {}".format(start_value))
            lines.append("End time: {}".format(end_value))

    cameras = command.get("cameras")
    if cameras:
        if isinstance(cameras, str):
            camera_label = cameras.replace(",", ", ")
        else:
            camera_label = ", ".join(str(camera) for camera in cameras)
        lines.append("Cameras: {}".format(camera_label))

    lines.append("Official: {}".format("Ano" if harddisk_save else "Ne"))
    return "\n".join(lines)


def send_registered_email(to, body=None, video_name=None, eta_range_minutes=None, command=None, harddisk_save=False):
    if body is None:
        if video_name:
            body = "Your request for video '{}' was registered and queued for processing. ".format(video_name)
        else:
            body = "Your request was registered and queued for processing. "
            
        body += "That means the SKV Server is up and running and is now processing (not only) your videos. "
        body += "You should get an email once the processing is done or if it fails. "

        if eta_range_minutes is not None:
            lower, upper = eta_range_minutes
            body += "\n\nBased on the current queue, the estimated completion time:\napproximately {}-{} minutes.".format(lower, upper)

        body += "\n\n"
        body += _format_registered_request_summary(
            video_name=video_name,
            command=command,
            harddisk_save=harddisk_save,
        )
        body += "\n\n"
        body += "If you got this email without submitting any command, please contact Miroslav Purkrabek (miroslav.purkrabek@skvflorbal.cz)."

    send_email(
        to=to,
        bcc=developer_email,
        body=body,
        subject="[SKV Video Server] Request queued"
    )
    
    


if __name__ == "__main__":
    config_path = "logging_config.yml"
    with open(config_path, "r") as f:
        config_data = yaml.safe_load(f.read())
        logging.config.dictConfig(config_data)
    logger = logging.getLogger("EmailSender")
    
    send_email(
        to=developer_email,
        cc="purkrabekspeedy@seznam.cz",
        bcc="mira.purkrabek@gmail.com",
    )
