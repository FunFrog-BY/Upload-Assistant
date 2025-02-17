import os
import re
import glob
import time
import ffmpeg
import random
import json
import platform
import asyncio
import oxipng
from multiprocessing import Pool
from pymediainfo import MediaInfo
from src.console import console
from data.config import config  # Import here to avoid dependency issues

img_host = [
    config["DEFAULT"][key].lower()
    for key in sorted(config["DEFAULT"].keys())
    if key.startswith("img_host_1")
]
screens = int(config['DEFAULT'].get('screens', 6))
task_limit = config['DEFAULT'].get('process_limit', "0")

try:
    task_limit = int(task_limit)  # Convert to integer
except ValueError:
    task_limit = 0
tone_map = config['DEFAULT'].get('tone_map', False)
optimize_images = config['DEFAULT'].get('optimize_images', True)


async def sanitize_filename(filename):
    # Replace invalid characters like colons with an underscore
    return re.sub(r'[<>:"/\\|?*]', '_', filename)


async def disc_screenshots(meta, filename, bdinfo, folder_id, base_dir, use_vs, image_list, ffdebug, num_screens=None, force_screenshots=False):
    if meta['debug']:
        start_time = time.time()
    if 'image_list' not in meta:
        meta['image_list'] = []
    existing_images = [img for img in meta['image_list'] if isinstance(img, dict) and img.get('img_url', '').startswith('http')]

    if len(existing_images) >= meta.get('cutoff') and not force_screenshots:
        console.print("[yellow]There are already at least {} images in the image list. Skipping additional screenshots.".format(meta.get('cutoff')))
        return

    if num_screens is None:
        num_screens = screens
    if num_screens == 0 or len(image_list) >= num_screens:
        return

    sanitized_filename = await sanitize_filename(filename)
    length = 0
    file = None
    frame_rate = None
    for each in bdinfo['files']:
        # Calculate total length in seconds, including fractional part
        int_length = sum(float(x) * 60 ** i for i, x in enumerate(reversed(each['length'].split(':'))))

        if int_length > length:
            length = int_length
            for root, dirs, files in os.walk(bdinfo['path']):
                for name in files:
                    if name.lower() == each['file'].lower():
                        file = os.path.join(root, name)
                        break  # Stop searching once the file is found

    if 'video' in bdinfo and bdinfo['video']:
        fps_string = bdinfo['video'][0].get('fps', None)
        if fps_string:
            try:
                frame_rate = float(fps_string.split(' ')[0])  # Extract and convert to float
            except ValueError:
                console.print("[red]Error: Unable to parse frame rate from bdinfo['video'][0]['fps']")

    keyframe = 'nokey' if "VC-1" in bdinfo['video'][0]['codec'] or bdinfo['video'][0]['hdr_dv'] != "" else 'none'
    if meta['debug']:
        print(f"File: {file}, Length: {length}, Frame Rate: {frame_rate}")
    os.chdir(f"{base_dir}/tmp/{folder_id}")
    existing_screens = glob.glob(f"{sanitized_filename}-*.png")
    total_existing = len(existing_screens) + len(existing_images)
    if not force_screenshots:
        num_screens = max(0, screens - total_existing)
    else:
        num_screens = num_screens

    if num_screens == 0 and not force_screenshots:
        console.print('[bold green]Reusing existing screenshots. No additional screenshots needed.')
        return

    if meta['debug'] and not force_screenshots:
        console.print(f"[bold yellow]Saving Screens... Total needed: {screens}, Existing: {total_existing}, To capture: {num_screens}")

    tone_map = meta.get('tone_map', False)
    if tone_map and "HDR" in meta['hdr']:
        hdr_tonemap = True
    else:
        hdr_tonemap = False

    capture_tasks = []
    capture_results = []
    if use_vs:
        from src.vs import vs_screengn
        vs_screengn(source=file, encode=None, filter_b_frames=False, num=num_screens, dir=f"{base_dir}/tmp/{folder_id}/")
    else:
        if meta.get('ffdebug', False):
            loglevel = 'verbose'
        else:
            loglevel = 'quiet'

        ss_times = await valid_ss_time([], num_screens + 1, length, frame_rate)
        existing_indices = {int(p.split('-')[-1].split('.')[0]) for p in existing_screens}
        capture_tasks = [
            capture_disc_task(
                i,
                file,
                ss_times[i],
                os.path.abspath(f"{base_dir}/tmp/{folder_id}/{sanitized_filename}-{len(existing_indices) + i}.png"),
                keyframe,
                loglevel,
                hdr_tonemap
            )
            for i in range(num_screens + 1)
        ]

        results = await asyncio.gather(*capture_tasks)
        filtered_results = [r for r in results if isinstance(r, tuple) and len(r) == 2]

        if len(filtered_results) != len(results):
            console.print(f"[yellow]Warning: {len(results) - len(filtered_results)} capture tasks returned invalid results.")

        filtered_results.sort(key=lambda x: x[0])  # Ensure order is preserved
        capture_results = [r[1] for r in filtered_results if r[1] is not None]

        if capture_results and len(capture_results) > num_screens:
            try:
                smallest = min(capture_results, key=os.path.getsize)
                if meta['debug']:
                    console.print(f"[yellow]Removing smallest image: {smallest} ({os.path.getsize(smallest)} bytes)")
                os.remove(smallest)
                capture_results.remove(smallest)
            except Exception as e:
                console.print(f"[red]Error removing smallest image: {str(e)}")

        console.print(f"[green]Successfully captured {len(capture_results)} screenshots.")

        optimized_results = []
        valid_images = [image for image in capture_results if os.path.exists(image)]

        if not valid_images:
            console.print("[red]No valid images found for optimization.[/red]")
            return []

        # Dynamically determine the number of processes
        num_tasks = len(valid_images)
        max_cores = task_limit if task_limit > 0 else os.cpu_count() // 2
        num_workers = min(num_tasks, max_cores)  # Limit to number of tasks or available cores
        console.print("[yellow]Opimizing images")
        if meta['debug']:
            console.print(f"Using {num_workers} worker(s) for {len(valid_images)} image(s)")

        with Pool(processes=num_workers) as pool:
            optimized_results = pool.map(optimize_image_task, valid_images)

        optimized_results = [res for res in optimized_results if not res.startswith("Error")]

        console.print(f"[green]Successfully optimized {len(optimized_results)} images.")

        valid_results = []
        remaining_retakes = []
        for image_path in optimized_results:
            if "Error" in image_path:
                console.print(f"[red]{image_path}")
                continue

            retake = False
            image_size = os.path.getsize(image_path)
            if image_size <= 75000:
                console.print(f"[yellow]Image {image_path} is incredibly small, retaking.")
                retake = True
            elif "imgbb" in img_host and image_size <= 31000000:
                if meta['debug']:
                    console.print(f"[green]Image {image_path} meets size requirements for imgbb.[/green]")
            elif any(host in ["imgbox", "pixhost"] for host in img_host) and image_size <= 10000000:
                if meta['debug']:
                    console.print(f"[green]Image {image_path} meets size requirements for {img_host}.[/green]")
            elif any(host in ["ptpimg", "lensdump", "ptscreens", "oeimg"] for host in img_host):
                if meta['debug']:
                    console.print(f"[green]Image {image_path} meets size requirements for {img_host}.[/green]")
            else:
                console.print("[red]Image size does not meet requirements for your image host, retaking.")
                retake = True

            if retake:
                retry_attempts = 3
                for attempt in range(1, retry_attempts + 1):
                    console.print(f"[yellow]Retaking screenshot for: {image_path} (Attempt {attempt}/{retry_attempts})[/yellow]")
                    try:
                        index = int(image_path.rsplit('-', 1)[-1].split('.')[0])
                        if os.path.exists(image_path):
                            os.remove(image_path)

                        random_time = random.uniform(0, length)
                        screenshot_response = await capture_disc_task(
                            (index, file, random_time, image_path, keyframe, loglevel, hdr_tonemap)
                        )

                        optimize_image_task(screenshot_response)
                        new_size = os.path.getsize(screenshot_response)
                        valid_image = False

                        if "imgbb" in img_host and new_size > 75000 and new_size <= 31000000:
                            console.print(f"[green]Successfully retaken screenshot for: {screenshot_response} ({new_size} bytes)[/green]")
                            valid_image = True
                        elif new_size > 75000 and new_size <= 10000000 and any(host in ["imgbox", "pixhost"] for host in img_host):
                            console.print(f"[green]Successfully retaken screenshot for: {screenshot_response} ({new_size} bytes)[/green]")
                            valid_image = True
                        elif new_size > 75000 and any(host in ["ptpimg", "lensdump", "ptscreens", "oeimg", "zipline"] for host in img_host):
                            console.print(f"[green]Successfully retaken screenshot for: {screenshot_response} ({new_size} bytes)[/green]")
                            valid_image = True

                        if valid_image:
                            valid_results.append(screenshot_response)
                            break
                        else:
                            console.print(f"[red]Retaken image {screenshot_response} does not meet the size requirements for {img_host}. Retrying...[/red]")
                    except Exception as e:
                        console.print(f"[red]Error retaking screenshot for {image_path}: {e}[/red]")
                else:
                    console.print(f"[red]All retry attempts failed for {image_path}. Skipping.[/red]")
                    remaining_retakes.append(image_path)
            else:
                valid_results.append(image_path)

        if remaining_retakes:
            console.print(f"[red]The following images could not be retaken successfully: {remaining_retakes}[/red]")

    console.print(f"[green]Successfully captured {len(valid_results)} screenshots.")

    if meta['debug']:
        finish_time = time.time()
        console.print(f"Screenshots processed in {finish_time - start_time:.4f} seconds")


async def capture_disc_task(index, file, ss_time, image_path, keyframe, loglevel, hdr_tonemap):
    try:
        ff = ffmpeg.input(file, ss=ss_time, skip_frame=keyframe)
        if hdr_tonemap:
            ff = (
                ff
                .filter('zscale', transfer='linear')
                .filter('tonemap', tonemap='mobius', desat=8.0)
                .filter('zscale', transfer='bt709')
                .filter('format', 'rgb24')
            )
        command = (
            ff
            .output(image_path, vframes=1, pix_fmt="rgb24")
            .overwrite_output()
            .global_args('-loglevel', loglevel)
        )
        process = await asyncio.create_subprocess_exec(*command.compile(), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await process.communicate()
        if process.returncode == 0:
            return (index, image_path)
        else:
            console.print(f"[red]FFmpeg error capturing screenshot: {stderr.decode()}")
            return (index, None)  # Ensure tuple format
    except Exception as e:
        console.print(f"[red]Error capturing screenshot: {e}")
        return None


async def dvd_screenshots(meta, disc_num, num_screens=None, retry_cap=None):
    if 'image_list' not in meta:
        meta['image_list'] = []
    existing_images = [img for img in meta['image_list'] if isinstance(img, dict) and img.get('img_url', '').startswith('http')]

    if len(existing_images) >= meta.get('cutoff') and not retry_cap:
        console.print("[yellow]There are already at least {} images in the image list. Skipping additional screenshots.".format(meta.get('cutoff')))
        return
    screens = meta.get('screens', 6)
    if num_screens is None:
        num_screens = screens - len(existing_images)
    if num_screens == 0 or (len(meta.get('image_list', [])) >= screens and disc_num == 0):
        return

    if len(glob.glob(f"{meta['base_dir']}/tmp/{meta['uuid']}/{meta['discs'][disc_num]['name']}-*.png")) >= num_screens:
        i = num_screens
        console.print('[bold green]Reusing screenshots')
        return

    ifo_mi = MediaInfo.parse(f"{meta['discs'][disc_num]['path']}/VTS_{meta['discs'][disc_num]['main_set'][0][:2]}_0.IFO", mediainfo_options={'inform_version': '1'})
    sar = 1
    for track in ifo_mi.tracks:
        if track.track_type == "Video":
            if isinstance(track.duration, str):
                durations = [float(d) for d in track.duration.split(' / ')]
                length = max(durations) / 1000  # Use the longest duration
            else:
                length = float(track.duration) / 1000  # noqa #F841 # Convert to seconds

            par = float(track.pixel_aspect_ratio)
            dar = float(track.display_aspect_ratio)
            width = float(track.width)
            height = float(track.height)
            frame_rate = float(track.frame_rate)
    if par < 1:
        new_height = dar * height
        sar = width / new_height
        w_sar = 1
        h_sar = sar
    else:
        sar = par
        w_sar = sar
        h_sar = 1

    async def _is_vob_good(n, loops, num_screens):
        max_loops = 6
        fallback_duration = 300
        valid_tracks = []

        while loops < max_loops:
            try:
                vob_mi = MediaInfo.parse(
                    f"{meta['discs'][disc_num]['path']}/VTS_{main_set[n]}",
                    output='JSON'
                )
                vob_mi = json.loads(vob_mi)

                for track in vob_mi.get('media', {}).get('track', []):
                    duration = float(track.get('Duration', 0))
                    width = track.get('Width')
                    height = track.get('Height')

                    if duration > 1 and width and height:  # Minimum 1-second track
                        valid_tracks.append({
                            'duration': duration,
                            'track_index': n
                        })

                if valid_tracks:
                    # Sort by duration, take longest track
                    longest_track = max(valid_tracks, key=lambda x: x['duration'])
                    return longest_track['duration'], longest_track['track_index']

            except Exception as e:
                console.print(f"[red]Error parsing VOB {n}: {e}")

            n = (n + 1) % len(main_set)
            loops += 1

        return fallback_duration, 0

    main_set = meta['discs'][disc_num]['main_set'][1:] if len(meta['discs'][disc_num]['main_set']) > 1 else meta['discs'][disc_num]['main_set']
    os.chdir(f"{meta['base_dir']}/tmp/{meta['uuid']}")
    voblength, n = await _is_vob_good(0, 0, num_screens)
    ss_times = await valid_ss_time([], num_screens + 1, voblength, frame_rate)
    capture_tasks = []
    existing_images = 0
    existing_image_paths = []

    for i in range(num_screens + 1):
        image = f"{meta['base_dir']}/tmp/{meta['uuid']}/{meta['discs'][disc_num]['name']}-{i}.png"
        input_file = f"{meta['discs'][disc_num]['path']}/VTS_{main_set[i % len(main_set)]}"
        if os.path.exists(image) and not meta.get('retake', False):
            existing_images += 1
            existing_image_paths.append(image)

    if existing_images == num_screens and not meta.get('retake', False):
        console.print("[yellow]The correct number of screenshots already exists. Skipping capture process.")
        capture_results = existing_image_paths
        return
    else:
        for i in range(num_screens + 1):
            image = f"{meta['base_dir']}/tmp/{meta['uuid']}/{meta['discs'][disc_num]['name']}-{i}.png"
            input_file = f"{meta['discs'][disc_num]['path']}/VTS_{main_set[i % len(main_set)]}"
            if not os.path.exists(image) or meta.get('retake', False):
                capture_tasks.append(
                    capture_dvd_screenshot(
                        (i, input_file, image, ss_times[i], meta, width, height, w_sar, h_sar)
                    )
                )

        capture_results = []
        results = await asyncio.gather(*capture_tasks)
        filtered_results = [r for r in results if isinstance(r, tuple) and len(r) == 2]

        if len(filtered_results) != len(results):
            console.print(f"[yellow]Warning: {len(results) - len(filtered_results)} capture tasks returned invalid results.")

        filtered_results.sort(key=lambda x: x[0])  # Ensure order is preserved
        capture_results = [r[1] for r in filtered_results if r[1] is not None]

        if capture_results and len(capture_results) > num_screens:
            smallest = None
            smallest_size = float('inf')
            for screens in glob.glob1(f"{meta['base_dir']}/tmp/{meta['uuid']}/", f"{meta['discs'][disc_num]['name']}-*"):
                screen_path = os.path.join(f"{meta['base_dir']}/tmp/{meta['uuid']}/", screens)
                try:
                    screen_size = os.path.getsize(screen_path)
                    if screen_size < smallest_size:
                        smallest_size = screen_size
                        smallest = screen_path
                except FileNotFoundError:
                    console.print(f"[red]File not found: {screen_path}[/red]")  # Handle potential edge cases
                    continue

            if smallest:
                if meta['debug']:
                    console.print(f"[yellow]Removing smallest image: {smallest} ({smallest_size} bytes)[/yellow]")
                os.remove(smallest)

        optimized_results = []

        # Filter out non-existent files first
        valid_images = [image for image in capture_results if os.path.exists(image)]

        # Dynamically determine the number of processes
        num_tasks = len(valid_images)
        max_cores = task_limit if task_limit > 0 else os.cpu_count() // 2
        num_workers = min(num_tasks, max_cores)  # Limit to number of tasks or available cores

        if num_workers == 0:
            console.print("[red]No valid images found for optimization.[/red]")
            return
        else:
            console.print("[yellow]Now optimizing images")

        if meta['debug']:
            console.print(f"Using {num_workers} worker(s) for {num_tasks} image(s)")

        # Set up multiprocessing pool with the determined number of workers
        with Pool(processes=num_workers) as pool:
            optimized_results = pool.map(optimize_image_task, valid_images)

        optimized_results = [res for res in optimized_results if not isinstance(res, str) or not res.startswith("Error")]

        console.print(f"[green]Successfully optimized {len(optimized_results)} images.")

        valid_results = []
        remaining_retakes = []

        for image in optimized_results:
            if "Error" in image:
                console.print(f"[red]{image}")
                continue

            retake = False
            image_size = os.path.getsize(image)
            if image_size <= 120000:
                console.print(f"[yellow]Image {image} is incredibly small, retaking.")
                retake = True

            if retake:
                retry_attempts = 3
                for attempt in range(1, retry_attempts + 1):
                    console.print(f"[yellow]Retaking screenshot for: {image} (Attempt {attempt}/{retry_attempts})[/yellow]")

                    index = int(image.rsplit('-', 1)[-1].split('.')[0])
                    input_file = f"{meta['discs'][disc_num]['path']}/VTS_{main_set[index % len(main_set)]}"
                    adjusted_time = random.uniform(0, voblength)

                    if os.path.exists(image):  # Prevent unnecessary deletion error
                        try:
                            os.remove(image)
                        except Exception as e:
                            console.print(f"[red]Failed to delete {image}: {e}[/red]")
                            break

                    try:
                        # Ensure `capture_dvd_screenshot()` always returns a tuple
                        screenshot_response = await capture_dvd_screenshot(
                            (index, input_file, image, adjusted_time, meta, width, height, w_sar, h_sar)
                        )

                        # Ensure it is a tuple before unpacking
                        if not isinstance(screenshot_response, tuple) or len(screenshot_response) != 2:
                            console.print(f"[red]Failed to capture screenshot for {image}. Retrying...[/red]")
                            continue

                        index, screenshot_result = screenshot_response  # Safe unpacking

                        if screenshot_result is None:
                            console.print(f"[red]Failed to capture screenshot for {image}. Retrying...[/red]")
                            continue

                        optimize_image_task(screenshot_result)

                        retaken_size = os.path.getsize(screenshot_result)
                        if retaken_size > 75000:
                            console.print(f"[green]Successfully retaken screenshot for: {screenshot_result} ({retaken_size} bytes)[/green]")
                            valid_results.append(screenshot_result)
                            break
                        else:
                            console.print(f"[red]Retaken image {screenshot_result} is still too small. Retrying...[/red]")
                    except Exception as e:
                        console.print(f"[red]Error capturing screenshot for {input_file} at {adjusted_time}: {e}[/red]")

                else:
                    console.print(f"[red]All retry attempts failed for {image}. Skipping.[/red]")
                    remaining_retakes.append(image)
            else:
                valid_results.append(image)
        if remaining_retakes:
            console.print(f"[red]The following images could not be retaken successfully: {remaining_retakes}[/red]")

    console.print(f"[green]Successfully captured {len(valid_results)} screenshots.")


async def capture_dvd_screenshot(task):
    index, input_file, image, seek_time, meta, width, height, w_sar, h_sar = task

    try:
        loglevel = 'verbose' if meta.get('ffdebug', False) else 'quiet'
        media_info = MediaInfo.parse(input_file)
        video_duration = next((track.duration for track in media_info.tracks if track.track_type == "Video"), None)

        if video_duration and seek_time > video_duration:
            seek_time = max(0, video_duration - 1)

        # Construct ffmpeg command
        ff = ffmpeg.input(input_file, ss=seek_time)
        if w_sar != 1 or h_sar != 1:
            ff = ff.filter('scale', int(round(width * w_sar)), int(round(height * h_sar)))

        cmd = ff.output(image, vframes=1, pix_fmt="rgb24").overwrite_output().global_args('-loglevel', loglevel, '-accurate_seek').compile()

        # Run ffmpeg asynchronously
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            console.print(f"[red]Error capturing screenshot for {input_file} at {seek_time}s:[/red]\n{stderr.decode()}")
            return (index, None)

        if os.path.exists(image):
            return (index, image)
        else:
            console.print(f"[red]Screenshot creation failed for {image}[/red]")
            return (index, None)

    except Exception as e:
        console.print(f"[red]Error capturing screenshot for {input_file} at {seek_time}s: {e}[/red]")
        return (index, None)


async def screenshots(path, filename, folder_id, base_dir, meta, num_screens=None, force_screenshots=False, manual_frames=None):
    """Screenshot capture function using concurrent.futures"""
    if meta['debug']:
        start_time = time.time()
        console.print("Image Host:", img_host)
    if 'image_list' not in meta:
        meta['image_list'] = []

    existing_images = [img for img in meta['image_list'] if isinstance(img, dict) and img.get('img_url', '').startswith('http')]

    if len(existing_images) >= meta.get('cutoff') and not force_screenshots:
        console.print("[yellow]There are already at least {} images in the image list. Skipping additional screenshots.".format(meta.get('cutoff')))
        return

    if num_screens is None:
        num_screens = screens - len(existing_images)
    if num_screens <= 0:
        return

    try:
        with open(f"{base_dir}/tmp/{folder_id}/MediaInfo.json", encoding='utf-8') as f:
            mi = json.load(f)
            video_track = mi['media']['track'][1]
            length = float(video_track.get('Duration', mi['media']['track'][0]['Duration']))
            width = float(video_track.get('Width'))
            height = float(video_track.get('Height'))
            par = float(video_track.get('PixelAspectRatio', 1))
            dar = float(video_track.get('DisplayAspectRatio'))
            frame_rate = float(video_track.get('FrameRate', 24.0)) if isinstance(video_track.get('FrameRate'), str) else 24.0

            if par == 1:
                sar = w_sar = h_sar = 1
            elif par < 1:
                new_height = dar * height
                sar = width / new_height
                w_sar = 1
                h_sar = sar
            else:
                sar = w_sar = par
                h_sar = 1
    except Exception as e:
        console.print(f"[red]Error processing MediaInfo.json: {e}")
        return

    loglevel = 'verbose' if meta.get('ffdebug', False) else 'quiet'
    os.chdir(f"{base_dir}/tmp/{folder_id}")

    if manual_frames:
        if meta['debug']:
            console.print(f"[yellow]Using manual frames: {manual_frames}")
        manual_frames = [int(frame) for frame in manual_frames.split(',')]
        ss_times = [frame / frame_rate for frame in manual_frames]
    else:
        ss_times = await valid_ss_time([], num_screens + 1, length, frame_rate, exclusion_zone=500)
    if meta['debug']:
        console.print(f"[green]Final list of frames for screenshots: {ss_times}")

    sanitized_filename = await sanitize_filename(filename)
    existing_images = 0
    existing_image_paths = []
    for i in range(num_screens + 1):
        image_path = os.path.abspath(f"{base_dir}/tmp/{folder_id}/{sanitized_filename}-{i}.png")
        if os.path.exists(image_path) and not meta.get('retake', False):
            existing_images += 1
            existing_image_paths.append(image_path)

    tone_map = meta.get('tone_map', False)
    if tone_map and "HDR" in meta['hdr']:
        hdr_tonemap = True
    else:
        hdr_tonemap = False

    capture_tasks = []
    if existing_images == num_screens and not meta.get('retake', False):
        console.print("[yellow]The correct number of screenshots already exists. Skipping capture process.")
        capture_results = existing_image_paths
        return
    else:
        for i in range(num_screens + 1):
            image_path = os.path.abspath(f"{base_dir}/tmp/{folder_id}/{sanitized_filename}-{i}.png")
            if not os.path.exists(image_path) or meta.get('retake', False):
                capture_tasks.append(
                    capture_screenshot(
                        (i, path, ss_times[i], image_path, width, height, w_sar, h_sar, loglevel, hdr_tonemap)
                    )
                )

        capture_results = []
        results = await asyncio.gather(*capture_tasks)
        filtered_results = [r for r in results if isinstance(r, tuple) and len(r) == 2]

        if len(filtered_results) != len(results):
            console.print(f"[yellow]Warning: {len(results) - len(filtered_results)} capture tasks returned invalid results.")

        filtered_results.sort(key=lambda x: x[0])  # Ensure order is preserved
        capture_results = [r[1] for r in filtered_results if r[1] is not None]

        if capture_results and len(capture_results) > num_screens:
            try:
                smallest = min(capture_results, key=os.path.getsize)
                if meta['debug']:
                    console.print(f"[yellow]Removing smallest image: {smallest} ({os.path.getsize(smallest)} bytes)")
                os.remove(smallest)
                capture_results.remove(smallest)
            except Exception as e:
                console.print(f"[red]Error removing smallest image: {str(e)}")

        console.print(f"[green]Successfully captured {len(capture_results)} screenshots.")

        optimized_results = []

        # Filter out non-existent files first
        valid_images = [image for image in capture_results if os.path.exists(image)]

        # Dynamically determine the number of processes
        num_tasks = len(valid_images)
        max_cores = task_limit if task_limit > 0 else os.cpu_count() // 2
        num_workers = min(num_tasks, max_cores)  # Limit to number of tasks or available cores

        if num_workers == 0:
            console.print("[red]No valid images found for optimization.[/red]")
            return
        else:
            console.print("[yellow]Now optimizing images")

        if meta['debug']:
            console.print(f"Using {num_workers} worker(s) for {num_tasks} image(s)")

        # Set up multiprocessing pool with the determined number of workers
        with Pool(processes=num_workers) as pool:
            optimized_results = pool.map(optimize_image_task, valid_images)

        optimized_results = [res for res in optimized_results if not isinstance(res, str) or not res.startswith("Error")]

        if meta['debug']:
            console.print("Optimized results:", optimized_results)
        console.print(f"[green]Successfully optimized {len(optimized_results)} images.")

        valid_results = []
        remaining_retakes = []
        for image_path in optimized_results:
            if "Error" in image_path:
                console.print(f"[red]{image_path}")
                continue
            retake = False
            image_size = os.path.getsize(image_path)
            if meta['debug']:
                console.print("Image paths:", image_path)
                console.print("Image sizes", image_size)
            if not manual_frames:
                if image_size <= 75000:
                    console.print(f"[yellow]Image {image_path} is incredibly small, retaking.")
                    retake = True
                elif "imgbb" in img_host and image_size <= 31000000:
                    if meta['debug']:
                        console.print(f"[green]Image {image_path} meets size requirements for imgbb.[/green]")
                elif any(host in ["imgbox", "pixhost"] for host in img_host) and image_size <= 10000000:
                    if meta['debug']:
                        console.print(f"[green]Image {image_path} meets size requirements for {img_host}.[/green]")
                elif any(host in ["ptpimg", "lensdump", "ptscreens", "oeimg", "zipline"] for host in img_host):
                    if meta['debug']:
                        console.print(f"[green]Image {image_path} meets size requirements for {img_host}.[/green]")
                else:
                    console.print("[red]Image size does not meet requirements for your image host, retaking.")
                    retake = True

            if retake:
                retry_attempts = 3
                for attempt in range(1, retry_attempts + 1):
                    console.print(f"[yellow]Retaking screenshot for: {image_path} (Attempt {attempt}/{retry_attempts})[/yellow]")
                    try:
                        index = int(image_path.rsplit('-', 1)[-1].split('.')[0])
                        if os.path.exists(image_path):
                            os.remove(image_path)
                        random_time = random.uniform(0, length)
                        screenshot_response = await capture_disc_task(
                            (index, path, random_time, image_path, width, height, w_sar, h_sar, loglevel, hdr_tonemap)
                        )

                        optimize_image_task(screenshot_response)
                        new_size = os.path.getsize(screenshot_response)
                        valid_image = False

                        if "imgbb" in img_host and new_size > 75000 and new_size <= 31000000:
                            console.print(f"[green]Successfully retaken screenshot for: {screenshot_response} ({new_size} bytes)[/green]")
                            valid_image = True
                        elif new_size > 75000 and new_size <= 10000000 and any(host in ["imgbox", "pixhost"] for host in img_host):
                            console.print(f"[green]Successfully retaken screenshot for: {screenshot_response} ({new_size} bytes)[/green]")
                            valid_image = True
                        elif new_size > 75000 and any(host in ["ptpimg", "lensdump", "ptscreens", "oeimg"] for host in img_host):
                            console.print(f"[green]Successfully retaken screenshot for: {screenshot_response} ({new_size} bytes)[/green]")
                            valid_image = True

                        if valid_image:
                            valid_results.append(screenshot_response)
                            break
                        else:
                            console.print(f"[red]Retaken image {screenshot_response} does not meet the size requirements for {img_host}. Retrying...[/red]")
                    except Exception as e:
                        console.print(f"[red]Error retaking screenshot for {image_path}: {e}[/red]")
                else:
                    console.print(f"[red]All retry attempts failed for {image_path}. Skipping.[/red]")
                    remaining_retakes.append(image_path)
            else:
                console.print("Validated result:", valid_results)
                valid_results.append(image_path)

        if remaining_retakes:
            console.print(f"[red]The following images could not be retaken successfully: {remaining_retakes}[/red]")

        console.print(f"[green]Successfully captured {len(valid_results)} screenshots.")

    if meta['debug']:
        finish_time = time.time()
        console.print(f"Screenshots processed in {finish_time - start_time:.4f} seconds")


async def capture_screenshot(args):
    index, path, ss_time, image_path, width, height, w_sar, h_sar, loglevel, hdr_tonemap = args
    try:
        if width <= 0 or height <= 0:
            return "Error: Invalid width or height for scaling"

        if ss_time < 0:
            return f"Error: Invalid timestamp {ss_time}"

        ff = ffmpeg.input(path, ss=ss_time)
        if w_sar != 1 or h_sar != 1:
            ff = ff.filter('scale', int(round(width * w_sar)), int(round(height * h_sar)))

        if hdr_tonemap:
            ff = (
                ff
                .filter('zscale', transfer='linear')
                .filter('tonemap', tonemap='mobius', desat=10.0)
                .filter('zscale', transfer='bt709')
                .filter('format', 'rgb24')
            )

        command = (
            ff
            .output(
                image_path,
                vframes=1,
                pix_fmt="rgb24"
            )
            .overwrite_output()
            .global_args('-loglevel', loglevel)
        )

        process = await asyncio.create_subprocess_exec(*command.compile(), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await process.communicate()
        if process.returncode == 0:
            return (index, image_path)
        else:
            console.print(f"[red]FFmpeg error capturing screenshot: {stderr.decode()}")
            return (index, None)  # Ensure tuple format
    except Exception as e:
        return f"Error: {str(e)}"


async def valid_ss_time(ss_times, num_screens, length, frame_rate, exclusion_zone=None):
    total_screens = num_screens + 1

    if exclusion_zone is None:
        exclusion_zone = max(length / (3 * total_screens), length / 15)

    result_times = ss_times.copy()
    section_size = (round(4 * length / 5) - round(length / 5)) / total_screens * 1.3
    section_starts = [round(length / 5) + i * (section_size * 0.9) for i in range(total_screens)]

    for section_index in range(total_screens):
        valid_time = False
        attempts = 0
        start_frame = round(section_starts[section_index] * frame_rate)
        end_frame = round((section_starts[section_index] + section_size) * frame_rate)

        while not valid_time and attempts < 50:
            attempts += 1
            frame = random.randint(start_frame, end_frame)
            time = frame / frame_rate

            if all(abs(frame - existing_time * frame_rate) > exclusion_zone * frame_rate for existing_time in result_times):
                result_times.append(time)
                valid_time = True

        if not valid_time:
            midpoint_frame = (start_frame + end_frame) // 2
            result_times.append(midpoint_frame / frame_rate)

    result_times = sorted(result_times)

    return result_times


def optimize_image_task(image):
    try:
        if optimize_images:
            os.environ['RAYON_NUM_THREADS'] = '1'
            if not os.path.exists(image):
                print(f"ERROR: File not found - {image}")
                return f"Error: File not found - {image}"

            pyver = platform.python_version_tuple()
            if int(pyver[0]) == 3 and int(pyver[1]) >= 7:
                level = 6 if os.path.getsize(image) >= 16000000 else 2
                oxipng.optimize(image, level=level)

            return image
        else:
            return image
    except Exception as e:
        print(f"ERROR optimizing {image}: {e}")
        return f"Error: {e}"
