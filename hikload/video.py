import ffmpeg
import os
import logging

import time
from datetime import datetime

logger = logging.getLogger('VideoHandler')

def concat_channel_videos(channel_metadata: dict, cid, args):
    start_time = time.perf_counter()

    # Create temporary text file as the FFmpeg requires it
    with open("tmp_list.txt", "w") as fl:

        audio_codec = True
        for filename in channel_metadata["filenames"]:
            fl.write("file '{}'\n".format(filename))
            
            # Find out if the file has audio stream 
            has_audio = False
            probe = ffmpeg.probe(filename)
            for stream in probe["streams"]:
                if stream["codec_type"].lower() == "audio":
                    has_audio = True
                    break
            audio_codec = audio_codec and has_audio

    logger.debug("Created temporary txt file for videos concatenation")
    
    if args.videoname != "":
        outname = "{}_{}_concat.{}".format(
            args.videoname,
            cid,
            args.videoformat
        )
    else:
        outname = "{}_{}_concat.{}".format(
            channel_metadata["startTime"].replace("-", "").replace(":", "").replace("Z", "").replace("T", ""),
            cid,
            args.videoformat
        )

    logger.debug("Concatenating {:d} videos of channel {}".format(len(channel_metadata['filenames']), cid))
    logger.debug("FFMPEG command: input-'{}', outname-'{}', codec copy, safe=0".format("tmp_list.txt", outname))
    try:
        (
            ffmpeg
            .input('tmp_list.txt', f='concat', safe=0)
            .output(outname, codec='copy')
            .overwrite_output()
            .global_args('-loglevel', 'error')
            .run(capture_stderr=True)
        )
    except ffmpeg._run.Error:
        # The FFmpeg thrown error while running
        # This could be because of unsupported audio codec (or the audio is missing)
        # Try to cocatenate without the audio stream
        try:
            (
                ffmpeg
                .input('tmp_list.txt', f='concat', safe=0)
                .output(outname, vcodec='copy')
                .overwrite_output()
                .global_args('-loglevel', 'error')
                .run(capture_stderr=True)
            )
        except ffmpeg._run.Error as e:
            logger.error("FFMpeg error: {}".format(e))

    # Clean up after yourself
    os.remove("tmp_list.txt")
    for filename in channel_metadata["filenames"]:
        os.remove(filename)

    end_time = time.perf_counter()
    run_time = end_time - start_time
    logger.debug("Recordings ({:d}) of the channel '{}' concatenated into video '{}' in {:.2f} seconds".format(
            len(channel_metadata['filenames']),
            cid,
            outname,
            run_time
    ))
    return outname


def cut_video(video_name, channel_metadata: dict):
    start_time = time.perf_counter()
    # Cut videos to required duration
    starttime = datetime.strptime(
        channel_metadata["startTime"], "%Y-%m-%dT%H:%M:%SZ")
    minstarttime = datetime.strptime(
        channel_metadata["minStartTime"], "%Y-%m-%dT%H:%M:%SZ")
    trim_start =  starttime - minstarttime 
    
    logger.debug("Triming video {}".format(video_name))
    outname = video_name.replace("_concat", "")
    (
        ffmpeg
        .input(video_name, ss=trim_start, t=channel_metadata["duration"])
        .output(outname, codec='copy')
        .overwrite_output()
        .global_args('-loglevel', 'error')
        .run()
    )

    # Clean up after yourself
    os.remove(video_name)

    end_time = time.perf_counter()
    run_time = end_time - start_time
    logger.debug("Video {} trimed into video {} in {:.2f} seconds".format(video_name, outname, run_time))
    return outname
