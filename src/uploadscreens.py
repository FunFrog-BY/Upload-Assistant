from src.console import console
from data.config import config
import os
import pyimgbox
import asyncio
import requests
import glob
import base64
import time
import re
import gc
import json


def upload_image_task(args):
    image, img_host, config, meta = args
    try:
        timeout = 60  # Default timeout
        img_url, raw_url, web_url = None, None, None

        if img_host == "imgbox":
            try:
                image_list = asyncio.run(imgbox_upload(os.getcwd(), [image], meta, return_dict={}))
                if image_list and all(
                    'img_url' in img and 'raw_url' in img and 'web_url' in img for img in image_list
                ):
                    img_url = image_list[0]['img_url']
                    raw_url = image_list[0]['raw_url']
                    web_url = image_list[0]['web_url']
                else:
                    return {
                        'status': 'failed',
                        'reason': "Imgbox upload failed. No valid URLs returned."
                    }
            except Exception as e:
                return {
                    'status': 'failed',
                    'reason': f"Error during Imgbox upload: {str(e)}"
                }

        elif img_host == "ptpimg":
            payload = {
                'format': 'json',
                'api_key': config['DEFAULT']['ptpimg_api']
            }

            with open(image, 'rb') as file:
                files = [('file-upload[0]', file)]
                headers = {'referer': 'https://ptpimg.me/index.php'}

                try:
                    response = requests.post(
                        "https://ptpimg.me/upload.php", headers=headers, data=payload, files=files, timeout=timeout
                    )
                    response.raise_for_status()  # Raise an exception for HTTP errors
                    response_data = response.json()

                    if not response_data or not isinstance(response_data, list) or 'code' not in response_data[0]:
                        return {'status': 'failed', 'reason': "Invalid JSON response from ptpimg"}

                    code = response_data[0]['code']
                    ext = response_data[0]['ext']
                    img_url = f"https://ptpimg.me/{code}.{ext}"
                    raw_url = img_url
                    web_url = img_url

                except requests.exceptions.Timeout:
                    return {'status': 'failed', 'reason': 'Request timed out'}
                except requests.exceptions.RequestException as e:
                    return {'status': 'failed', 'reason': f"Request failed: {str(e)}"}
                except json.JSONDecodeError:
                    return {'status': 'failed', 'reason': 'Invalid JSON response from ptpimg'}

        elif img_host == "imgbb":
            url = "https://api.imgbb.com/1/upload"
            try:
                with open(image, "rb") as img_file:
                    encoded_image = base64.b64encode(img_file.read()).decode('utf8')

                data = {
                    'key': config['DEFAULT']['imgbb_api'],
                    'image': encoded_image,
                }

                response = requests.post(url, data=data, timeout=timeout)
                response_data = response.json()
                if response.status_code != 200 or not response_data.get('success'):
                    console.print("[yellow]imgbb failed, trying next image host")
                    return {'status': 'failed', 'reason': 'imgbb upload failed'}

                img_url = response_data['data'].get('medium', {}).get('url') or response_data['data']['thumb']['url']
                raw_url = response_data['data']['image']['url']
                web_url = response_data['data']['url_viewer']

                if meta['debug']:
                    console.print(f"[green]Image URLs: img_url={img_url}, raw_url={raw_url}, web_url={web_url}")

                return {'status': 'success', 'img_url': img_url, 'raw_url': raw_url, 'web_url': web_url}

            except requests.exceptions.Timeout:
                console.print("[red]Request timed out. The server took too long to respond.")
                return {'status': 'failed', 'reason': 'Request timed out'}

            except ValueError as e:  # JSON decoding error
                console.print(f"[red]Invalid JSON response: {e}")
                return {'status': 'failed', 'reason': 'Invalid JSON response'}

            except requests.exceptions.RequestException as e:
                console.print(f"[red]Request failed with error: {e}")
                return {'status': 'failed', 'reason': str(e)}

        elif img_host == "dalexni":
            url = "https://dalexni.com/1/upload"
            try:
                with open(image, "rb") as img_file:
                    encoded_image = base64.b64encode(img_file.read()).decode('utf8')

                data = {
                    'key': config['DEFAULT']['dalexni_api'],
                    'image': encoded_image,
                }

                response = requests.post(url, data=data, timeout=timeout)
                response_data = response.json()
                if response.status_code != 200 or not response_data.get('success'):
                    console.print("[yellow]DALEXNI failed, trying next image host")
                    return {'status': 'failed', 'reason': 'DALEXNI upload failed'}

                img_url = response_data['data'].get('medium', {}).get('url') or response_data['data']['thumb']['url']
                raw_url = response_data['data']['image']['url']
                web_url = response_data['data']['url_viewer']

                if meta['debug']:
                    console.print(f"[green]Image URLs: img_url={img_url}, raw_url={raw_url}, web_url={web_url}")

                return {'status': 'success', 'img_url': img_url, 'raw_url': raw_url, 'web_url': web_url}

            except requests.exceptions.Timeout:
                console.print("[red]Request timed out. The server took too long to respond.")
                return {'status': 'failed', 'reason': 'Request timed out'}

            except ValueError as e:  # JSON decoding error
                console.print(f"[red]Invalid JSON response: {e}")
                return {'status': 'failed', 'reason': 'Invalid JSON response'}

            except requests.exceptions.RequestException as e:
                console.print(f"[red]Request failed with error: {e}")
                return {'status': 'failed', 'reason': str(e)}

        elif img_host == "ptscreens":
            url = "https://ptscreens.com/api/1/upload"
            try:
                files = {
                    'source': ('file-upload[0]', open(image, 'rb')),
                }
                headers = {
                    'X-API-Key': config['DEFAULT']['ptscreens_api']
                }
                response = requests.post(url, headers=headers, files=files, timeout=timeout)
                response_data = response.json()
                if response_data.get('status_code') != 200:
                    console.print("[yellow]ptscreens failed, trying next image host")
                    return {'status': 'failed', 'reason': 'ptscreens upload failed'}

                img_url = response_data['image']['medium']['url']
                raw_url = response_data['image']['url']
                web_url = response_data['image']['url_viewer']
                if meta['debug']:
                    console.print(f"[green]Image URLs: img_url={img_url}, raw_url={raw_url}, web_url={web_url}")

            except requests.exceptions.Timeout:
                console.print("[red]Request timed out. The server took too long to respond.")
                return {'status': 'failed', 'reason': 'Request timed out'}
            except requests.exceptions.RequestException as e:
                console.print(f"[red]Request failed with error: {e}")
                return {'status': 'failed', 'reason': str(e)}

        elif img_host == "oeimg":
            url = "https://imgoe.download/api/1/upload"
            try:
                data = {
                    'image': base64.b64encode(open(image, "rb").read()).decode('utf8')
                }
                headers = {
                    'X-API-Key': config['DEFAULT']['oeimg_api'],
                }
                response = requests.post(url, data=data, headers=headers, timeout=timeout)
                response_data = response.json()
                if response.status_code != 200 or not response_data.get('success'):
                    console.print("[yellow]OEimg failed, trying next image host")
                    return {'status': 'failed', 'reason': 'OEimg upload failed'}

                img_url = response_data['data']['image']['url']
                raw_url = response_data['data']['image']['url']
                web_url = response_data['data']['url_viewer']
                if meta['debug']:
                    console.print(f"[green]Image URLs: img_url={img_url}, raw_url={raw_url}, web_url={web_url}")

            except requests.exceptions.Timeout:
                console.print("[red]Request timed out. The server took too long to respond.")
                return {'status': 'failed', 'reason': 'Request timed out'}
            except requests.exceptions.RequestException as e:
                console.print(f"[red]Request failed with error: {e}")
                return {'status': 'failed', 'reason': str(e)}

        elif img_host == "pixhost":
            url = "https://api.pixhost.to/images"
            data = {
                'content_type': '0',
                'max_th_size': 350
            }
            files = {
                'img': ('file-upload[0]', open(image, 'rb'))
            }
            response = requests.post(url, data=data, files=files, timeout=timeout)
            response_data = response.json()
            if response.status_code == 200:
                raw_url = response_data['th_url'].replace('https://t', 'https://img').replace('/thumbs/', '/images/')
                img_url = response_data['th_url']
                web_url = response_data['show_url']

        elif img_host == "lensdump":
            url = "https://lensdump.com/api/1/upload"
            data = {
                'image': base64.b64encode(open(image, "rb").read()).decode('utf8')
            }
            headers = {
                'X-API-Key': config['DEFAULT']['lensdump_api']
            }
            response = requests.post(url, data=data, headers=headers, timeout=timeout)
            response_data = response.json()
            if response_data.get('status_code') == 200:
                img_url = response_data['data']['image']['url']
                raw_url = response_data['data']['image']['url']
                web_url = response_data['data']['url_viewer']

        elif img_host == "zipline":
            url = config['DEFAULT'].get('zipline_url')
            api_key = config['DEFAULT'].get('zipline_api_key')

            if not url or not api_key:
                console.print("[red]Error: Missing Zipline URL or API key in config.")
                return {'status': 'failed', 'reason': 'Missing Zipline URL or API key'}

            try:
                with open(image, "rb") as img_file:
                    files = {'file': img_file}
                    headers = {
                        'Authorization': f'{api_key}',
                    }

                    response = requests.post(url, files=files, headers=headers, timeout=timeout)
                    if response.status_code == 200:
                        response_data = response.json()
                        if 'files' in response_data:
                            img_url = response_data['files'][0]
                            raw_url = img_url.replace('/u/', '/r/')
                            web_url = img_url.replace('/u/', '/r/')
                            return {
                                'status': 'success',
                                'img_url': img_url,
                                'raw_url': raw_url,
                                'web_url': web_url
                            }
                        else:
                            return {'status': 'failed', 'reason': 'No valid URL returned from Zipline'}

                    else:
                        return {'status': 'failed', 'reason': f"Zipline upload failed: {response.text}"}
            except requests.exceptions.Timeout:
                console.print("[red]Request timed out. The server took too long to respond.")
                return {'status': 'failed', 'reason': 'Request timed out'}

            except ValueError as e:  # JSON decoding error
                console.print(f"[red]Invalid JSON response: {e}")
                return {'status': 'failed', 'reason': 'Invalid JSON response'}

            except requests.exceptions.RequestException as e:
                console.print(f"[red]Request failed with error: {e}")
                return {'status': 'failed', 'reason': str(e)}

        if img_url and raw_url and web_url:
            return {
                'status': 'success',
                'img_url': img_url,
                'raw_url': raw_url,
                'web_url': web_url,
                'local_file_path': image
            }
        else:
            return {
                'status': 'failed',
                'reason': f"Failed to upload image to {img_host}. No URLs received."
            }

    except Exception as e:
        return {
            'status': 'failed',
            'reason': str(e)
        }


async def upload_screens(meta, screens, img_host_num, i, total_screens, custom_img_list, return_dict, retry_mode=False, max_retries=3):
    if meta['debug']:
        upload_start_time = time.time()

    os.chdir(f"{meta['base_dir']}/tmp/{meta['uuid']}")
    initial_img_host = config['DEFAULT'][f'img_host_{img_host_num}']
    img_host = meta['imghost']
    using_custom_img_list = isinstance(custom_img_list, list) and bool(custom_img_list)

    if 'image_sizes' not in meta:
        meta['image_sizes'] = {}

    if using_custom_img_list:
        image_glob = custom_img_list
        existing_images = []
        existing_count = 0
    else:
        image_glob = glob.glob("*.png")
        unwanted_patterns = ["FILE*", "PLAYLIST*", "POSTER*"]
        unwanted_files = set()
        for pattern in unwanted_patterns:
            unwanted_files.update(glob.glob(pattern))

        image_glob = [file for file in image_glob if file not in unwanted_files]
        image_glob = list(set(image_glob))

        # Sort `image_glob` by numeric suffix to ensure correct order
        def extract_numeric_suffix(filename):
            match = re.search(r"-(\d+)\.png$", filename)
            return int(match.group(1)) if match else float('inf')  # Place non-matching files at the end

        image_glob.sort(key=extract_numeric_suffix)

        if meta['debug']:
            console.print("image globs (sorted):", image_glob)

        existing_images = [img for img in meta['image_list'] if img.get('img_url') and img.get('web_url')]
        existing_count = len(existing_images)

    if not retry_mode:
        images_needed = max(0, total_screens - existing_count)
    else:
        images_needed = total_screens

    if existing_count >= total_screens and not retry_mode and img_host == initial_img_host and not using_custom_img_list:
        console.print(f"[yellow]Skipping upload because enough images are already uploaded to {img_host}. Existing images: {existing_count}, Required: {total_screens}")
        return meta['image_list'], total_screens

    upload_tasks = [
        (index, image, img_host, config, meta)
        for index, image in enumerate(image_glob[:images_needed])
    ]

    default_pool_size = len(upload_tasks)
    host_limits = {"oeimg": 6, "ptscreens": 1, "lensdump": 1}
    pool_size = host_limits.get(img_host, default_pool_size)
    max_workers = min(len(upload_tasks), pool_size)

    results = []
    semaphore = asyncio.Semaphore(max_workers)

    async def async_upload(task):
        """Wrapper function to run upload_image_task with concurrency control."""
        index, *task_args = task
        async with semaphore:
            try:
                result = await asyncio.to_thread(upload_image_task, task_args)

                if result.get('status') == 'success':
                    return (index, result)
                else:
                    console.print(f"[red]{result}")
                    return None
            except Exception as e:
                console.print(f"[red]Error during upload: {str(e)}")
                return None

    upload_results = await asyncio.gather(*[async_upload(task) for task in upload_tasks])
    results = [res for res in upload_results if res is not None]
    results.sort(key=lambda x: x[0])

    successfully_uploaded = [(index, result) for index, result in results if result['status'] == 'success']

    if len(successfully_uploaded) < meta.get('cutoff', 1) and not retry_mode and img_host == initial_img_host and not using_custom_img_list:
        img_host_num += 1
        if f'img_host_{img_host_num}' in config['DEFAULT']:
            meta['imghost'] = config['DEFAULT'][f'img_host_{img_host_num}']
            console.print(f"[cyan]Switching to the next image host: {config['DEFAULT'][f'img_host_{img_host_num}']}[/cyan]")

            gc.collect()

            return await upload_screens(meta, screens, img_host_num, i, total_screens, custom_img_list, return_dict, retry_mode=True)

        else:
            console.print("[red]No more image hosts available. Aborting upload process.")
            return meta['image_list'], len(meta['image_list'])

    new_images = []
    for index, upload in successfully_uploaded:
        raw_url = upload['raw_url']
        new_image = {
            'img_url': upload['img_url'],
            'raw_url': raw_url,
            'web_url': upload['web_url']
        }
        new_images.append(new_image)
        if not using_custom_img_list and raw_url not in {img['raw_url'] for img in meta['image_list']}:
            if meta['debug']:
                console.print(f"[blue]Adding {raw_url} to image_list")
            meta['image_list'].append(new_image)
            local_file_path = upload.get('local_file_path')
            if local_file_path:
                image_size = os.path.getsize(local_file_path)
                meta['image_sizes'][raw_url] = image_size

    console.print(f"[green]Successfully uploaded {len(new_images)} images.")

    if meta['debug']:
        console.print(f"Screenshot uploads processed in {time.time() - upload_start_time:.4f} seconds")

    if using_custom_img_list:
        return new_images, len(new_images)

    return meta['image_list'], len(successfully_uploaded)


async def imgbox_upload(chdir, image_glob, meta, return_dict):
    try:
        os.chdir(chdir)
        image_list = []

        async with pyimgbox.Gallery(thumb_width=350, square_thumbs=False) as gallery:
            for image in image_glob:
                try:
                    async for submission in gallery.add([image]):
                        if not submission['success']:
                            console.print(f"[red]Error uploading to imgbox: [yellow]{submission['error']}[/yellow][/red]")
                        else:
                            web_url = submission.get('web_url')
                            img_url = submission.get('thumbnail_url')
                            raw_url = submission.get('image_url')
                            if web_url and img_url and raw_url:
                                image_dict = {
                                    'web_url': web_url,
                                    'img_url': img_url,
                                    'raw_url': raw_url
                                }
                                image_list.append(image_dict)
                            else:
                                console.print(f"[red]Incomplete URLs received for image: {image}")
                except Exception as e:
                    console.print(f"[red]Error during upload for {image}: {str(e)}")

        return_dict['image_list'] = image_list
        return image_list

    except Exception as e:
        console.print(f"[red]An error occurred while uploading images to imgbox: {str(e)}")
        return []
