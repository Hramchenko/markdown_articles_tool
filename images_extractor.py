#!/usr/bin/python3

"""
Simple script to download images and replace image links in markdown documents.
"""

import argparse
import hashlib
from mimetypes import guess_extension, types_map
import os
import re
import requests
import unicodedata

from typing import Optional, List

from pkg.transformers.md.transformer import ArticleTransformer


__version__ = '0.0.2'


del types_map['.jpe']


def slugify(value):
    """
    Normalizes string, converts to lowercase, removes non-alpha characters,
    and converts spaces to hyphens.
    """

    value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore')
    value = re.sub(r'[^\w\s-]', '', value.decode()).strip().lower()
    value = re.sub(r'[-\s]+', '-', value)

    return value


def get_filename_from_url(req: requests.Response) -> Optional[str]:
    """
    Get filename from url and, if not found, try to get from content-disposition.
    """

    if req.url.find('/'):
        result = req.url.rsplit('/', 1)[1]
    else:
        cd = req.headers.get('content-disposition')

        if cd is None:
            return None

        file_name = re.findall('filename=(.+)', cd)

        if len(file_name) == 0:
            return None

        result = file_name[0]

    f_name, f_ext = os.path.splitext(result)

    result = f'{slugify(f_name)}{guess_extension(req.headers["content-type"].partition(";")[0].strip())}' if not f_ext\
        else f'{slugify(f_name)}.{slugify(f_ext)}'

    return result


class ImageDownloader:
    allowed_url_prefixes = {'http', 'ftp'}

    def __init__(self, article_path: str, skip_list: Optional[List[str]] = None, skip_all_errors: bool = False,
                 img_dir_name: str = 'images', img_public_path: str = '', downloading_timeout: float = -1,
                 deduplication: bool = False):
        self._img_dir_name = img_dir_name
        self._img_public_path = img_public_path
        self._article_file_path = article_path
        self._skip_list = set(skip_list) if skip_list is not None else []
        self._images_dir = os.path.join(os.path.dirname(self._article_file_path), self._img_dir_name)
        self._skip_all_errors = skip_all_errors
        self._downloading_timeout = downloading_timeout
        self._deduplication = deduplication

    def download_images(self, images: List[str]) -> dict:
        replacement_mapping = {}
        hash_to_path_mapping = {}
        skip_list = self._skip_list
        img_count = len(images)
        path_join = os.path.join
        img_dir_name = self._img_dir_name
        img_public_path = self._img_public_path
        images_dir = self._images_dir
        deduplication = self._deduplication

        try:
            os.makedirs(self._images_dir)
        except FileExistsError:
            # Existing directory is not error.
            pass

        for img_num, img_url in enumerate(images):
            assert img_url not in replacement_mapping.keys(), f'BUG: already downloaded image "{img_url}"...'

            if img_url in skip_list:
                print(f'Image {img_num + 1} ["{img_url}"] was skipped, because it\'s in the skip list...')
                continue

            if not self._is_allowed_url_prefix(img_url):
                print(f'Image {img_num + 1} ["{img_url}"] was skipped, because it has incorrect URL...')
                continue

            print(f'Downloading image {img_num + 1} of {img_count} from "{img_url}"...')

            try:
                img_response = self._download_image(img_url)
            except Exception as e:
                if self._skip_all_errors:
                    print(f'Warning: can\'t download image {img_num + 1}, error: [{str(e)}], '
                          'but processing will be continued, because `skip_all_errors` flag is set')
                    continue
                raise

            img_filename = get_filename_from_url(img_response)
            image_content = img_response.content

            if deduplication:
                # path_to_hash_mapping.
                new_content_hash = hashlib.sha256(image_content).digest()
                existed_file_name = hash_to_path_mapping.get(new_content_hash)
                if existed_file_name is not None:
                    img_filename = existed_file_name
                    document_img_path = path_join(img_public_path or img_dir_name, img_filename)
                    replacement_mapping.setdefault(img_url, document_img_path)
                    continue
                else:
                    hash_to_path_mapping[new_content_hash] = img_filename

            document_img_path = path_join(img_public_path or img_dir_name, img_filename)
            img_filename, document_img_path = self._correct_paths(replacement_mapping, document_img_path, img_url,
                                                                  img_filename)

            real_img_path = path_join(images_dir, img_filename)
            replacement_mapping.setdefault(img_url, document_img_path)

            ImageDownloader._write_image(real_img_path, image_content)

        return replacement_mapping

    def _download_image(self, image_url: str):
        """
        Download image file from the URL.
        :param image_url: URL to download.
        """

        try:
            img_response = requests.get(image_url, allow_redirects=True, timeout=self._downloading_timeout)
        except requests.exceptions.SSLError:
            print('Incorrect SSL certificate, trying to download without verifying...')
            img_response = requests.get(image_url, allow_redirects=True, verify=False,
                                        timeout=self._downloading_timeout)

        if img_response.status_code != 200:
            raise OSError(str(img_response))

        return img_response

    @staticmethod
    def _write_image(img_path: str, data: bytes):
        """
        Write image data into the file.
        """

        print(f'Image will be written to the file "{img_path}"...')
        with open(img_path, 'wb') as img_file:
            img_file.write(data)
            img_file.close()

    def _is_allowed_url_prefix(self, url: str) -> bool:
        for prefix in self.allowed_url_prefixes:
            if url.startswith(prefix):
                return True

        return False

    def _correct_paths(self, replacement_mapping, document_img_path, img_url, img_filename):
        # Images can have similar name, but different URLs, but I want to save original filename, if possible.
        for url, path in replacement_mapping.items():
            if document_img_path == path and img_url != url:
                img_filename = f'{hashlib.md5(img_url.encode()).hexdigest()}_{img_filename}'
                document_img_path = os.path.join(self._img_public_path or self._img_dir_name, img_filename)
                break

        return img_filename, document_img_path


def main(arguments):
    """
    Entrypoint.
    """

    article_path = os.path.expanduser(arguments.article_file_path)
    skip_list = arguments.skip_list
    skip_all = arguments.skip_all_incorrect

    print('Processing started...')

    if isinstance(skip_list, str):
        if skip_list.startswith('@'):
            skip_list = skip_list[1:]
            print(f'Reading skip list from a file "{skip_list}"...')
            with open(os.path.expanduser(skip_list), 'r') as fsl:
                skip_list = [s.strip() for s in fsl.readlines()]
        else:
            skip_list = [s.strip() for s in skip_list.split(',')]

    ArticleTransformer(article_path,
                       ImageDownloader(
                           article_path=article_path,
                           skip_list=skip_list,
                           skip_all_errors=skip_all,
                           img_dir_name=arguments.images_dirname,
                           img_public_path=arguments.images_publicpath,
                           downloading_timeout=arguments.downloading_timeout,
                           deduplication=arguments.dedup_with_hash
                           )
                       ).run()

    print('Processing finished successfully...')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('article_file_path', type=str,
                        help='path to the article file in the Markdown format')
    parser.add_argument('-s', '--skip-list', default=None,
                        help='skip URL\'s from the comma-separated list (or file with a leading \'@\')')
    parser.add_argument('-d', '--images-dirname', default='images',
                        help='Folder in which to download images')
    parser.add_argument('-p', '--images-publicpath', default='',
                        help='Public path to the folder of downloaded images')
    parser.add_argument('-a', '--skip-all-incorrect', default=False, action='store_true',
                        help='skip all incorrect images')
    parser.add_argument('-t', '--downloading-timeout', type=float, default=-1,
                        help='how many seconds to wait before downloading will be failed')
    parser.add_argument('-D', '--dedup-with-hash', default=False, action='store_true',
                        help='Deduplicate images, using content hash')
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}', help='return version number')

    args = parser.parse_args()

    main(args)
