# coding=utf-8
# Author: Danilo Daloiso
# Modified by Danny89530
# URL: https://sickrage.github.io
#
# This file is part of SickRage.
#
# SickRage is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# SickRage is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with SickRage. If not, see <http://www.gnu.org/licenses/>.

from __future__ import print_function, unicode_literals

import re
import traceback

from sickbeard import db, logger, tvcache
from sickbeard.bs4_parser import BS4Parser
from sickbeard.common import Quality
from sickbeard.name_parser.parser import InvalidNameException, InvalidShowException, NameParser
from sickrage.helper.common import convert_size, try_int
from sickrage.providers.torrent.TorrentProvider import TorrentProvider


class TNTVillageProvider(TorrentProvider):  # pylint: disable=too-many-instance-attributes

    def __init__(self):

        TorrentProvider.__init__(self, "TNTVillage")

        self._uid = None
        self._hash = None
        self.cat = None
        self.engrelease = None
        self.page = 1
        self.subtitle = None
        self.minseed = None
        self.minleech = None

        self.category_dict = {'Serie TV': 29,
                              'Cartoni': 8,
                              'Anime': 7,
                              'Programmi e Film TV': 1,
                              'Documentari': 14,
                              'All': 0}

        self.urls = {'base_url': 'http://forum.tntvillage.scambioetico.org',
                     'search_page': 'http://tntvillage.scambioetico.org/src/releaselist.php',
                     'download': 'http://forum.tntvillage.scambioetico.org/index.php?act=Attach&type=post&id=%s'}

        self.url = self.urls['base_url']

        self.sub_string = ['sub', 'softsub']

        self.proper_strings = ['PROPER', 'REPACK']

        self.categories = "cat=29"

        self.cache = tvcache.TVCache(self, min_time=30)  # only poll TNTVillage every 30 minutes max

    @staticmethod
    def _reverseQuality(quality):

        quality_string = ''

        if quality == Quality.SDTV:
            quality_string = ' HDTV x264'
        if quality == Quality.SDDVD:
            quality_string = ' DVDRIP'
        elif quality == Quality.HDTV:
            quality_string = ' 720p HDTV x264'
        elif quality == Quality.FULLHDTV:
            quality_string = ' 1080p HDTV x264'
        elif quality == Quality.RAWHDTV:
            quality_string = ' 1080i HDTV mpeg2'
        elif quality == Quality.HDWEBDL:
            quality_string = ' 720p WEB-DL h264'
        elif quality == Quality.FULLHDWEBDL:
            quality_string = ' 1080p WEB-DL h264'
        elif quality == Quality.HDBLURAY:
            quality_string = ' 720p Bluray x264'
        elif quality == Quality.FULLHDBLURAY:
            quality_string = ' 1080p Bluray x264'

        return quality_string

    @staticmethod
    def _episodeQuality(quality_string):  # pylint: disable=too-many-return-statements, too-many-branches
        """
            Return The quality from the scene episode HTML row.
        """

        def checkName(options, func):
            return func([re.search(option, quality_string, re.I) for option in options])

        dvdOptions = checkName(["dvd", "dvdrip", "dvdmux", "DVD9", "DVD5"], any)
        bluRayOptions = checkName(["BD", "BDmux", "BDrip", "BRrip", "Bluray"], any)
        sdOptions = checkName(["h264", "divx", "XviD", "tv", "TVrip", "SATRip", "DTTrip", "Mpeg2"], any)
        hdOptions = checkName(["720p"], any)
        fullHD = checkName(["1080p", "fullHD"], any)
        webdl = checkName(["webdl", "webmux", "webrip", "dl-webmux", "web-dlmux", "webdl-mux", "web-dl", "webdlmux", "dlmux"], any)

        if sdOptions and not dvdOptions and not fullHD and not hdOptions:
            return Quality.SDTV
        elif dvdOptions:
            return Quality.SDDVD
        elif hdOptions and not bluRayOptions and not fullHD and not webdl:
            return Quality.HDTV
        elif not hdOptions and not bluRayOptions and fullHD and not webdl:
            return Quality.FULLHDTV
        elif hdOptions and not bluRayOptions and not fullHD and webdl:
            return Quality.HDWEBDL
        elif not hdOptions and not bluRayOptions and fullHD and webdl:
            return Quality.FULLHDWEBDL
        elif bluRayOptions and hdOptions and not fullHD:
            return Quality.HDBLURAY
        elif bluRayOptions and fullHD and not hdOptions:
            return Quality.FULLHDBLURAY
        else:
            return Quality.UNKNOWN

    def _is_italian(self, torrent_title, extra_info):

        if not torrent_title or torrent_title == 'None':
            return False

        if not extra_info or extra_info == 'None':
            return False

        subFound = italian = False
        for sub in self.sub_string:
            if re.search(sub, extra_info, re.I):
                subFound = True
            else:
                continue

            if re.search("[ -_.|]ita[ -_.|]", extra_info.lower().split(sub)[0], re.I):
                logger.log("Found Italian release: '%s'" % torrent_title, logger.DEBUG)
                italian = True
                break

        if not subFound and re.search("ita", extra_info, re.I):
            logger.log("Found Italian release: '%s'" % torrent_title, logger.DEBUG)
            italian = True

        return italian

    @staticmethod
    def _is_english(torrent_title, extra_info):

        if not torrent_title or torrent_title == 'None':
            return False

        if not extra_info or extra_info == 'None':
            return False

        english = False
        if re.search("eng", extra_info, re.I):
            logger.log("Found English release:  " + torrent_title, logger.DEBUG)
            english = True

        return english

    @staticmethod
    def _is_season_pack(name):

        try:
            parse_result = NameParser(tryIndexers=True).parse(name)
        except (InvalidNameException, InvalidShowException) as error:
            logger.log("{0}".format(error), logger.DEBUG)
            return False

        main_db_con = db.DBConnection()
        sql_selection = "select count(*) as count from tv_episodes where showid = ? and season = ?"
        episodes = main_db_con.select(sql_selection, [parse_result.show.indexerid, parse_result.season_number])
        if int(episodes[0][b'count']) == len(parse_result.episode_numbers):
            return True

    def search(self, search_params, age=0, ep_obj=None):  # pylint: disable=too-many-locals, too-many-branches, too-many-statements
        results = []

        for mode in search_params:
            items = []
            logger.log("Search Mode: {0}".format(mode), logger.DEBUG)

            for search_string in search_params[mode]:

                if search_string == '':
                    continue

                search_string = str(search_string).replace('.', ' ')

                logger.log("Search string: {0}".format
                           (search_string.decode("utf-8")), logger.DEBUG)

                params = {'srcrel': search_string,
                          'cat': str(self.cat),
                          'page': str(self.page)}

                data = self.get_url(self.urls['search_page'],
                                    post_data=params,
                                    returns='text')

                if not data:
                    logger.log("No data returned from provider", logger.DEBUG)
                    continue

                try:
                    with BS4Parser(data, 'html5lib') as html:
                        last_page = int(html.find('div', class_='pagination')('li')[-1].attrs['p'])

                    if last_page == 0:
                        logger.log("Data returned from provider does not contain any torrents", logger.DEBUG)
                        continue

                    for page in range(self.page, last_page + 1):

                        if page != 1:
                            params = {'srcrel': search_string,
                                      'cat': str(self.cat),
                                      'page': str(page)}

                            data = self.get_url(self.urls['search_page'],
                                                post_data=params,
                                                returns='text')

                            if not data:
                                logger.log("No data returned from provider", logger.DEBUG)
                                continue

                        with BS4Parser(data, 'html5lib') as html:
                            torrent_table = html.find('div', class_='showrelease_tb')
                            torrent_rows = torrent_table('tr') if torrent_table else []

                            logger.log("Inspecting page {0} of {1}. {2} Torrents found on this page".format(page, last_page, len(torrent_rows) - 1), logger.DEBUG)

                            # Continue only if one Release is found
                            if len(torrent_rows) < 2:
                                logger.log("Data returned from provider does not contain any torrents", logger.DEBUG)
                                continue

                            for result in torrent_table('tr')[1:]:

                                try:
                                    # link = result('td')[6].find('a')['href']
                                    title = result('td')[6].text.replace(u'\xa0', u' ').replace('.', ' ')
                                    download_url = self.urls['download'] % result('td')[0].find('a')['href'][-8:]
                                    leechers = int(result('td')[3].text)
                                    seeders = int(result('td')[4].text)
                                    # torrent_size = result('td')[3]('td')[3].text.strip('[]') + " GB"
                                    # size = convert_size(torrent_size) or -1
                                    size = -1
                                except (AttributeError, TypeError):
                                    continue

                                torrent_info = re.split(r'(\[.*?\])', title)[1]
                                filename_qt = self._reverseQuality(self._episodeQuality(torrent_info))

                                if Quality.nameQuality(title) == Quality.UNKNOWN:
                                    title += filename_qt

                                if not self._is_italian(title, torrent_info) and not self.subtitle:
                                    logger.log("Torrent is subtitled, skipping: {0} ".format(title), logger.DEBUG)
                                    continue

                                if self.engrelease and not self._is_english(tile, torrent_info):
                                    logger.log("Torrent isnt english audio/subtitled , skipping: {0} ".format(title), logger.DEBUG)
                                    continue

                                search_show = re.split(r'([Ss][\d{1,2}]+)', search_string)[0]
                                show_title = search_show
                                rindex = re.search(r'([Ss][\d{1,2}]+)', title)
                                if rindex:
                                    show_title = title[:rindex.start()]
                                    ep_params = title[rindex.start():].decode("utf-8").split(' ')[0].upper()
                                # if show_title.lower() != search_show.lower() and search_show.lower() in show_title.lower():
                                    new_title = search_show + ep_params + filename_qt
                                    title = new_title

                                if not all([title, download_url]):
                                    continue

                                if self._is_season_pack(title.replace(filename_qt, '')):
                                    title = re.sub(r'([Ee][\d{1,2}\-?]+)', '', title)

                                # Filter unseeded torrent
                                if seeders < self.minseed or leechers < self.minleech:
                                    if mode != 'RSS':
                                        logger.log("Discarding torrent because it doesn't meet the minimum seeders or leechers: {0} (S:{1} L:{2})".format
                                                   (title, seeders, leechers), logger.DEBUG)
                                    continue

                                item = {'title': title, 'link': download_url, 'size': size, 'seeders': seeders, 'leechers': leechers, 'hash': ''}
                                if mode != 'RSS':
                                    logger.log("Found result: {0} with {1} seeders and {2} leechers".format(title, seeders, leechers), logger.DEBUG)

                                items.append(item)

                except Exception:
                    logger.log("Failed parsing provider for getPage. Traceback: {0}".format(traceback.format_exc()), logger.ERROR)

                # For each search mode sort all the items by seeders if available if available
                items.sort(key=lambda d: try_int(d.get('seeders', 0)), reverse=True)

                results += items

        return results


provider = TNTVillageProvider()
