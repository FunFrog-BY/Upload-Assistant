# -*- coding: utf-8 -*-
# import discord
import asyncio
import requests
import os
import re
import platform
import cli_ui
import httpx
from src.trackers.COMMON import COMMON
from src.console import console
from src.rehostimages import check_hosts


class HUNO():
    """
    Edit for Tracker:
        Edit BASE.torrent with announce and source
        Check for duplicates
        Set type/category IDs
        Upload
    """
    def __init__(self, config):
        self.config = config
        self.tracker = 'HUNO'
        self.source_flag = 'HUNO'
        self.search_url = 'https://hawke.uno/api/torrents/filter'
        self.upload_url = 'https://hawke.uno/api/torrents/upload'
        self.signature = "\n[center][url=https://github.com/Audionut/Upload-Assistant]Created by Audionut's Upload Assistant[/url][/center]"
        self.banned_groups = ["4K4U, Bearfish, BiTOR, BONE, D3FiL3R, d3g, DTR, ELiTE, EVO, eztv, EzzRips, FGT, HashMiner, HETeam, HEVCBay, HiQVE, HR-DR, iFT, ION265, iVy, JATT, Joy, LAMA, m3th, MeGusta, MRN, Musafirboy, OEPlus, Pahe.in, PHOCiS, PSA, RARBG, RMTeam, ShieldBearer, SiQ, TBD, Telly, TSP, VXT, WKS, YAWNiX, YIFY, YTS"]
        pass

    async def upload(self, meta, disctype):
        common = COMMON(config=self.config)
        huno_name = await self.get_name(meta)
        if huno_name == "SKIPPED":
            console.print("[bold red]Skipping upload to HUNO due to missing audio language")
            return

        url_host_mapping = {
            "ibb.co": "imgbb",
            "ptpimg.me": "ptpimg",
            "pixhost.to": "pixhost",
            "imgbox.com": "imgbox",
            "imagebam.com": "bam",
        }
        approved_image_hosts = ['ptpimg', 'imgbox', 'imgbb', 'pixhost', 'bam']
        await check_hosts(meta, self.tracker, url_host_mapping=url_host_mapping, img_host_index=1, approved_image_hosts=approved_image_hosts)
        if 'HUNO_images_key' in meta:
            image_list = meta['HUNO_images_key']
        else:
            image_list = meta['image_list']
        await common.unit3d_edit_desc(meta, self.tracker, self.signature, image_list=image_list)
        await common.edit_torrent(meta, self.tracker, self.source_flag)
        cat_id = await self.get_cat_id(meta['category'])
        type_id = await self.get_type_id(meta)
        resolution_id = await self.get_res_id(meta['resolution'])
        if meta['anon'] == 0 and not self.config['TRACKERS'][self.tracker].get('anon', False):
            anon = 0
        else:
            anon = 1

        if meta['bdinfo'] is not None:
            mi_dump = None
            bd_dump = open(f"{meta['base_dir']}/tmp/{meta['uuid']}/BD_SUMMARY_00.txt", 'r', encoding='utf-8').read()
        else:
            mi_dump = open(f"{meta['base_dir']}/tmp/{meta['uuid']}/MEDIAINFO.txt", 'r', encoding='utf-8').read()
            bd_dump = None
        desc = open(f"{meta['base_dir']}/tmp/{meta['uuid']}/[HUNO]DESCRIPTION.txt", 'r', encoding='utf-8').read()
        open_torrent = open(f"{meta['base_dir']}/tmp/{meta['uuid']}/[HUNO]{meta['clean_name']}.torrent", 'rb')
        files = {'torrent': open_torrent}
        data = {
            'name': huno_name,
            'description': desc,
            'mediainfo': mi_dump,
            'bdinfo': bd_dump,
            'category_id': cat_id,
            'type_id': type_id,
            'resolution_id': resolution_id,
            'tmdb': meta['tmdb'],
            'imdb': meta['imdb_id'],
            'tvdb': meta['tvdb_id'],
            'mal': meta['mal_id'],
            'igdb': 0,
            'anonymous': anon,
            'stream': await self.is_plex_friendly(meta),
            'sd': meta['sd'],
            'keywords': meta['keywords'],
            'season_pack': meta.get('tv_pack', 0),
            # 'featured' : 0,
            # 'free' : 0,
            # 'double_up' : 0,
            # 'sticky' : 0,
        }

        tracker_config = self.config['TRACKERS'][self.tracker]

        if 'internal' in tracker_config:
            if tracker_config['internal'] and meta['tag'] and meta['tag'][1:] in tracker_config.get('internal_groups', []):
                data['internal'] = 1
            else:
                data['internal'] = 0

        headers = {
            'User-Agent': f'Upload Assistant/2.2 ({platform.system()} {platform.release()})'
        }
        params = {
            'api_token': tracker_config['api_key'].strip()
        }

        if meta['debug'] is False:
            response = requests.post(url=self.upload_url, files=files, data=data, headers=headers, params=params)
            try:
                console.print(response.json())
                # adding torrent link to comment of torrent file
                t_id = response.json()['data'].split(".")[1].split("/")[3]
                await common.add_tracker_torrent(meta, self.tracker, self.source_flag, self.config['TRACKERS'][self.tracker].get('announce_url'), "https://hawke.uno/torrents/" + t_id)
            except Exception:
                console.print("It may have uploaded, go check")
                return
        else:
            console.print("[cyan]Request Data:")
            console.print(data)
        open_torrent.close()

    def get_audio(self, meta):
        channels = meta.get('channels', "")
        codec = meta.get('audio', "").replace("DD+", "DDP").replace("EX", "").replace("Dual-Audio", "").replace(channels, "")
        dual = "Dual-Audio" in meta.get('audio', "")
        language = ""

        if dual:
            language = "Dual"
        else:
            if meta['is_disc'] == "BDMV":
                # Handle BDMV-specific functionality
                bdinfo = meta.get('bdinfo', {})
                audio_tracks = bdinfo.get("audio", [])
                languages = {track.get("language", "") for track in audio_tracks if "language" in track}

                if len(languages) > 1:
                    if not meta['unattended'] or (meta['unattended'] and meta.get('unattended-confirm', False)):
                        cli_ui.info(f"Multiple audio languages detected: {', '.join(languages)}")
                        if cli_ui.ask_yes_no("Is this a dual audio release?", default=True):
                            language = "Dual"
                    else:
                        language = "SKIPPED"

                elif languages:
                    language = languages
                else:
                    print("DEBUG: No languages found in BDMV audio tracks.")

            else:
                media_info_path = f"{meta['base_dir']}/tmp/{meta['uuid']}/MEDIAINFO.txt"
                with open(media_info_path, 'r', encoding='utf-8') as f:
                    media_info_text = f.read()

                # Extract all audio sections for DVD or other cases
                audio_sections = re.findall(r'Audio\s+.*?(?=\n\n|Text|Menu|$)', media_info_text, re.DOTALL)
                if audio_sections:
                    if meta['is_disc'] == "DVD":
                        # Aggregate all languages for DVDs
                        languages = []
                        for section in audio_sections:
                            language_match = re.search(r'Language\s*:\s*(\w+.*)', section)
                            if language_match:
                                lang = language_match.group(1).strip()
                                lang = re.sub(r'\(.+\)', '', lang)  # Remove parentheses and extra info
                                if lang not in languages:
                                    languages.append(lang)

                        # Combine languages if multiple are found
                        if len(languages) > 1:
                            language = "Dual"
                        elif languages:
                            language = languages[0]
                        else:
                            print("DEBUG: No languages found in audio sections.")
                    else:
                        # Use the first audio section for non-DVD cases
                        first_audio_section = audio_sections[0]
                        language_match = re.search(r'Language\s*:\s*(\w+.*)', first_audio_section)

                        if language_match:
                            language = language_match.group(1).strip()
                            language = re.sub(r'\(.+\)', '', language)
                        else:
                            print("DEBUG: No Language match found in the first audio section.")
                else:
                    print("DEBUG: No Audio sections found in MEDIAINFO.txt.")

        if language == "zxx":
            language = "Silent"
        elif not language:
            if not meta['unattended'] or (meta['unattended'] and meta.get('unattended-confirm', False)):
                language = cli_ui.ask_string('No audio language present, you must enter one:')
            else:
                language = "SKIPPED"

        return f'{codec} {channels} {language}'

    def get_basename(self, meta):
        path = next(iter(meta['filelist']), meta['path'])
        return os.path.basename(path)

    async def get_name(self, meta):
        # Copied from Prep.get_name() then modified to match HUNO's naming convention.
        # It was much easier to build the name from scratch than to alter the existing name.

        basename = self.get_basename(meta)
        hc = meta.get('hardcoded-subs')
        type = meta.get('type', "").upper()
        title = meta.get('title', "")
        alt_title = meta.get('aka', "")  # noqa F841
        year = meta.get('year', "")
        resolution = meta.get('resolution', "")
        audio = self.get_audio(meta)
        if "SKIPPED" in audio:
            return "SKIPPED"
        service = meta.get('service', "")
        season = meta.get('season', "")
        episode = meta.get('episode', "")
        repack = meta.get('repack', "")
        if repack.strip():
            repack = f"[{repack}]"
        three_d = meta.get('3D', "")
        tag = meta.get('tag', "").replace("-", "- ")
        if tag == "":
            tag = "- NOGRP"
        source = meta.get('source', "")
        uhd = meta.get('uhd', "")
        hdr = meta.get('hdr', "")
        if not hdr.strip():
            hdr = "SDR"
        distributor = meta.get('distributor', "")  # noqa F841
        video_codec = meta.get('video_codec', "")
        video_encode = meta.get('video_encode', "").replace(".", "")
        if 'x265' in basename:
            video_encode = video_encode.replace('H', 'x')
        region = meta.get('region', "")
        dvd_size = meta.get('dvd_size', "")
        edition = meta.get('edition', "")
        hybrid = "Hybrid" if "HYBRID" in basename.upper() else ""
        scale = "DS4K" if "DS4K" in basename.upper() else "RM4K" if "RM4K" in basename.upper() else ""

        # YAY NAMING FUN
        if meta['category'] == "MOVIE":  # MOVIE SPECIFIC
            if type == "DISC":  # Disk
                if meta['is_disc'] == 'BDMV':
                    name = f"{title} ({year}) {three_d} {edition} ({resolution} {region} {uhd} {source} {hybrid} {video_codec} {hdr} {audio} {tag}) {repack}"
                elif meta['is_disc'] == 'DVD':
                    name = f"{title} ({year}) {edition} ({resolution} {dvd_size} {hybrid} {video_codec} {hdr} {audio} {tag}) {repack}"
                elif meta['is_disc'] == 'HDDVD':
                    name = f"{title} ({year}) {edition} ({resolution} {source} {hybrid} {video_codec} {hdr} {audio} {tag}) {repack}"
            elif type == "REMUX" and source == "BluRay":  # BluRay Remux
                name = f"{title} ({year}) {three_d} {edition} ({resolution} {uhd} {source} {hybrid} REMUX {video_codec} {hdr} {audio} {tag}) {repack}"
            elif type == "REMUX" and source in ("PAL DVD", "NTSC DVD", "DVD"):  # DVD Remux
                name = f"{title} ({year}) {edition} ({resolution} DVD {hybrid} REMUX {video_codec} {hdr} {audio} {tag}) {repack}"
            elif type == "ENCODE":  # Encode
                name = f"{title} ({year}) {edition} ({resolution} {scale} {uhd} {source} {hybrid} {video_encode} {hdr} {audio} {tag}) {repack}"
            elif type in ("WEBDL", "WEBRIP"):  # WEB
                name = f"{title} ({year}) {edition} ({resolution} {scale} {uhd} {service} WEB-DL {hybrid} {video_encode} {hdr} {audio} {tag}) {repack}"
            elif type == "HDTV":  # HDTV
                name = f"{title} ({year}) {edition} ({resolution} HDTV {hybrid} {video_encode} {audio} {tag}) {repack}"
        elif meta['category'] == "TV":  # TV SPECIFIC
            if type == "DISC":  # Disk
                if meta['is_disc'] == 'BDMV':
                    name = f"{title} ({year}) {season}{episode} {three_d} {edition} ({resolution} {region} {uhd} {source} {hybrid} {video_codec} {hdr} {audio} {tag}) {repack}"
                if meta['is_disc'] == 'DVD':
                    name = f"{title} ({year}) {season}{episode} {edition} ({resolution} {dvd_size} {hybrid} {video_codec} {hdr} {audio} {tag}) {repack}"
                elif meta['is_disc'] == 'HDDVD':
                    name = f"{title} ({year}) {season}{episode} {edition} ({resolution} {source} {hybrid} {video_codec} {hdr} {audio} {tag}) {repack}"
            elif type == "REMUX" and source == "BluRay":  # BluRay Remux
                name = f"{title} ({year}) {season}{episode} {three_d} {edition} ({resolution} {uhd} {source} {hybrid} REMUX {video_codec} {hdr} {audio} {tag}) {repack}"  # SOURCE
            elif type == "REMUX" and source in ("PAL DVD", "NTSC DVD", "DVD"):  # DVD Remux
                name = f"{title} ({year}) {season}{episode} {edition} ({resolution} DVD {hybrid} REMUX {video_codec} {hdr} {audio} {tag}) {repack}"  # SOURCE
            elif type == "ENCODE":  # Encode
                name = f"{title} ({year}) {season}{episode} {edition} ({resolution} {scale} {uhd} {source} {hybrid} {video_encode} {hdr} {audio} {tag}) {repack}"  # SOURCE
            elif type in ("WEBDL", "WEBRIP"):  # WEB
                name = f"{title} ({year}) {season}{episode} {edition} ({resolution} {scale} {uhd} {service} WEB-DL {hybrid} {video_encode} {hdr} {audio} {tag}) {repack}"
            elif type == "HDTV":  # HDTV
                name = f"{title} ({year}) {season}{episode} {edition} ({resolution} HDTV {hybrid} {video_encode} {audio} {tag}) {repack}"

        if hc:
            name = re.sub(r'((\([0-9]{4}\)))', r'\1 Ensubbed', name)
        return ' '.join(name.split()).replace(": ", " - ")

    async def get_cat_id(self, category_name):
        category_id = {
            'MOVIE': '1',
            'TV': '2',
        }.get(category_name, '0')
        return category_id

    async def get_type_id(self, meta):
        type = meta.get('type').upper()
        video_encode = meta.get('video_encode')

        if type == 'REMUX':
            return '2'
        elif type in ('WEBDL', 'WEBRIP'):
            return '15' if 'x265' in video_encode else '3'
        elif type in ('ENCODE', 'HDTV'):
            return '15'
        elif type == 'DISC':
            return '1'
        else:
            return '0'

    async def get_res_id(self, resolution):
        resolution_id = {
            'Other': '10',
            '4320p': '1',
            '2160p': '2',
            '1080p': '3',
            '1080i': '4',
            '720p': '5',
            '576p': '6',
            '576i': '7',
            '480p': '8',
            '480i': '9'
        }.get(resolution, '10')
        return resolution_id

    async def is_plex_friendly(self, meta):
        lossy_audio_codecs = ["AAC", "DD", "DD+", "OPUS"]

        if any(l in meta["audio"] for l in lossy_audio_codecs):  # noqa E741
            return 1

        return 0

    async def search_existing(self, meta, disctype):
        if meta['video_codec'] != "HEVC" and meta['type'] in {"ENCODE", "WEBRIP", "DVDRIP", "HDTV"}:
            console.print('[bold red]Only x265/HEVC encodes are allowed at HUNO')
            meta['skipping'] = "HUNO"
            return
        dupes = []
        console.print("[yellow]Searching for existing torrents on HUNO...")

        params = {
            'api_token': self.config['TRACKERS']['HUNO']['api_key'].strip(),
            'tmdbId': meta['tmdb'],
            'categories[]': await self.get_cat_id(meta['category']),
            'types[]': await self.get_type_id(meta),
            'resolutions[]': await self.get_res_id(meta['resolution']),
            'name': ""
        }
        if meta['category'] == 'TV':
            params['name'] = f"{meta.get('season', '')}"
        if meta.get('edition', "") != "":
            params['name'] + meta['edition']
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(url=self.search_url, params=params)
                if response.status_code == 200:
                    data = response.json()
                    for each in data['data']:
                        result = [each][0]['attributes']['name']
                        dupes.append(result)
                else:
                    console.print(f"[bold red]Failed to search torrents. HTTP Status: {response.status_code}")
        except httpx.TimeoutException:
            console.print("[bold red]Request timed out after 5 seconds")
        except httpx.RequestError as e:
            console.print(f"[bold red]Unable to search for existing torrents: {e}")
        except Exception as e:
            console.print(f"[bold red]Unexpected error: {e}")
            await asyncio.sleep(5)

        return dupes
