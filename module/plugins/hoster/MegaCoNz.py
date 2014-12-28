# -*- coding: utf-8 -*-

import random
import re

from array import array
from base64 import standard_b64decode
from os import remove

from Crypto.Cipher import AES
from Crypto.Util import Counter
# from pycurl import SSL_CIPHER_LIST

from module.common.json_layer import json_loads, json_dumps
from module.plugins.Hoster import Hoster


############################ General errors ###################################
# EINTERNAL            (-1): An internal error has occurred. Please submit a bug report, detailing the exact circumstances in which this error occurred
# EARGS                (-2): You have passed invalid arguments to this command
# EAGAIN               (-3): (always at the request level) A temporary congestion or server malfunction prevented your request from being processed. No data was altered. Retry. Retries must be spaced with exponential backoff
# ERATELIMIT           (-4): You have exceeded your command weight per time quota. Please wait a few seconds, then try again (this should never happen in sane real-life applications)
#
############################ Upload errors ####################################
# EFAILED              (-5): The upload failed. Please restart it from scratch
# ETOOMANY             (-6): Too many concurrent IP addresses are accessing this upload target URL
# ERANGE               (-7): The upload file packet is out of range or not starting and ending on a chunk boundary
# EEXPIRED             (-8): The upload target URL you are trying to access has expired. Please request a fresh one
#
############################ Stream/System errors #############################
# ENOENT               (-9): Object (typically, node or user) not found
# ECIRCULAR           (-10): Circular linkage attempted
# EACCESS             (-11): Access violation (e.g., trying to write to a read-only share)
# EEXIST              (-12): Trying to create an object that already exists
# EINCOMPLETE         (-13): Trying to access an incomplete resource
# EKEY                (-14): A decryption operation failed (never returned by the API)
# ESID                (-15): Invalid or expired user session, please relogin
# EBLOCKED            (-16): User blocked
# EOVERQUOTA          (-17): Request over quota
# ETEMPUNAVAIL        (-18): Resource temporarily not available, please try again later
# ETOOMANYCONNECTIONS (-19): Too many connections on this resource
# EWRITE              (-20): Write failed
# EREAD               (-21): Read failed
# EAPPKEY             (-22): Invalid application key; request not processed


class MegaCoNz(Hoster):
    __name__    = "MegaCoNz"
    __type__    = "hoster"
    __version__ = "0.20"

    __pattern__ = r'https?://(?:www\.)?mega\.co\.nz/#(?P<TYPE>N|)!(?P<ID>[\w^_]+)!(?P<KEY>[\w,\\-]+)'

    __description__ = """Mega.co.nz hoster plugin"""
    __license__     = "GPLv3"
    __authors__     = [("RaNaN", "ranan@pyload.org"),
                       ("Walter Purcaro", "vuolter@gmail.com")]


    API_URL     = "https://eu.api.mega.co.nz/cs"
    FILE_SUFFIX = ".crypted"


    def b64_decode(self, data):
        data = data.replace("-", "+").replace("_", "/")
        return standard_b64decode(data + '=' * (-len(data) % 4))


    def getCipherKey(self, key):
        """ Construct the cipher key from the given data """
        a = array("I", key)

        k        = array("I", [a[0] ^ a[4], a[1] ^ a[5], a[2] ^ a[6], a[3] ^ a[7]])
        iv       = a[4:6] + (0, 0)
        meta_mac = a[6:8]

        return k, iv, meta_mac


    def api_response(self, **kwargs):
        """ Dispatch a call to the api, see https://mega.co.nz/#developers """

        # generate a session id, no idea where to obtain elsewhere
        uid = random.randint(10 << 9, 10 ** 10)

        res = self.load(self.API_URL, get={'id': uid}, post=json_dumps([kwargs]))
        self.logDebug("Api Response: " + res)
        return json_loads(res)


    def decryptAttr(self, data, key):
        k, iv, meta_mac = getCipherKey(key)
        cbc             = AES.new(k, AES.MODE_CBC, "\0" * 16)
        attr            = cbc.decrypt(self.b64_decode(data))

        self.logDebug("Decrypted Attr: " + attr)
        if not attr.startswith("MEGA"):
            self.fail(_("Decryption failed"))

        # Data is padded, 0-bytes must be stripped
        return json_loads(re.search(r'{.+?}', attr).group(0))


    def decryptFile(self, key):
        """  Decrypts the file at lastDownload` """

        # upper 64 bit of counter start
        n = key[16:24]

        # convert counter to long and shift bytes
        k, iv, meta_mac = getCipherKey(key)
        ctr             = Counter.new(128, initial_value=long(n.encode("hex"), 16) << 64)
        cipher          = AES.new(k, AES.MODE_CTR, counter=ctr)

        self.pyfile.setStatus("decrypting")

        file_crypted   = self.lastDownload
        file_decrypted = file_crypted.rsplit(self.FILE_SUFFIX)[0]

        try:
            f  = open(file_crypted, "rb")
            df = open(file_decrypted, "wb")

        except IOError, e:
            self.fail(str(e))

        chunk_size = 2 ** 15  # buffer size, 32k
        # file_mac   = [0, 0, 0, 0]  # calculate CBC-MAC for checksum

        while True:
            buf = f.read(chunk_size)
            if not buf:
                break

            chunk = cipher.decrypt(buf)
            df.write(chunk)

        f.close()
        df.close()

        remove(file_crypted)
        self.lastDownload = file_decrypted


    def process(self, pyfile):
        key = None

        # match is guaranteed because plugin was chosen to handle url
        pattern = re.match(self.__pattern__, pyfile.url).groupdict()
        node    = pattern['ID']
        key     = pattern['KEY']
        public  = pattern['TYPE'] != 'N'

        self.logDebug("ID: %s" % node, "Key: %s" % key, "Type: %s" % ("public" if public else "node"))

        key = self.b64_decode(key)

        # g is for requesting a download url
        # this is similar to the calls in the mega js app, documentation is very bad
        if public:
            dl = self.api_response(a="g", g=1, p=node, ssl=1)[0]
        else:
            dl = self.api_response(a="g", g=1, n=node, ssl=1)[0]

        if "e" in dl:
            ecode = -dl['e']

            if ecode in (9, 16, 21):
                self.offline()

            elif ecode in (3, 13, 17, 18, 19):
                self.tempOffline()

            elif ecode in (1, 4, 6, 10, 15, 21):
                self.retry(5, 30, _("Error code: [%s]") % -ecode)

            else:
                self.fail(_("Error code: [%s]") % -ecode)

        attr = self.decryptAttr(dl['at'], key)

        pyfile.name = attr['n'] + self.FILE_SUFFIX
        pyfile.size = dl['s']

        # self.req.http.c.setopt(SSL_CIPHER_LIST, "RC4-MD5:DEFAULT")

        self.download(dl['g'])

        self.decryptFile(key)

        # Everything is finished and final name can be set
        pyfile.name = attr['n']
