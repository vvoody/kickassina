# -*- coding: utf-8 -*-
import urllib
import logging
import re
from google.appengine.ext import db
from google.appengine.ext import webapp
from google.appengine.api import urlfetch
from django.utils import simplejson as json
from google.appengine.ext.webapp.util import run_wsgi_app

# 'README' will tell you how to fill the below 3 values
USER_ID=None  # user_id is immutable, better than screen_name
WEIBO_GSID=None
WEIBO_ST=None
# Be careful if you want to modify the request api url
TWITTER_REQAPI = 'http://api.twitter.com/1/statuses/user_timeline.json?user_id=%d&trim_user=true&include_rts=true&include_entities=true&since_id=%d'
# blocked by Weibo.com
INNOCENT_NETLOCS = ['goo.gl', 'bit.ly', 'is.gd', 'tinyurl.com']
# not show user ids in the tweet due to disturbing and security,
# replace "@username" with customized string("@nil" by default),
# instead string can be empty, that means remove all the mentions.
PLZ_HIDE_ID = True
PLZ_HIDE_ID_INSTEAD = "@x"
# Configuration END.
### DO NOT CHANGE THE FOLLOWING VALUES UNLESS YOU ARE SURE ###
WEIBO_KICK_URL='http://t.sina.cn/dpool/ttt/mblogDeal.php?st=%s&st=%s&gsid=%s' % (WEIBO_ST, WEIBO_ST, WEIBO_GSID)
WEIBO_HOME_URL='http://t.sina.cn/dpool/ttt/home.php?vt=1&gsid=%s' % WEIBO_GSID
WEIBO_KICK_URL_DEADLINE=30
WEIBO_HOME_URL_DEADLINE=12
TRIED_TIMES_MAX=5
UA='Opera/9.80 (Android; Linux; Opera Mobi/ADR-1011151731; U; en) Presto/2.5.28 Version/10.1'
###


class DB(db.Model):
    kicked = db.BooleanProperty(default=False)
    tweet_id = db.IntegerProperty()
    tweet = db.StringProperty(multiline=True)
    user_id = db.IntegerProperty()
    failed = db.BooleanProperty(default=False)    # bloody damn censorship or sina's block
    whyfailed = db.TextProperty()
    tried_times = db.IntegerProperty(default=0)
    tco_urls = db.StringListProperty()
    tco_expanded_urls = db.StringListProperty()


class MainPage(webapp.RequestHandler):
    def get(self):
        self.response.out.write("fair enough")


class WhyfailedPage(webapp.RequestHandler):
    def get_failed_tweets(self):
        return DB.all().filter('failed', True).order('-tweet_id')

    def get(self, tid):
        if tid:
            k = DB.get_by_id(int(tid))
            if k is not None:
                self.response.out.write("%s" % k.whyfailed)
        else:
            failed_tweets = self.get_failed_tweets()
            self.response.out.write("""
            <html>
            <body>""")

            for t in failed_tweets:
                id_ = t.key().id()
                self.response.out.write("""
                <a href="/whyfailed/%s">%d</a>
                """ % (id_, id_))

            self.response.out.write("""
            </body>
            </html>
            """)


class NexttweetHandler(webapp.RequestHandler):
    @classmethod
    def next_tweet(cls):
        # next tweet to be kicked
        return DB.all().filter('kicked', False).filter('failed', False).order('tweet_id').get()

    def get(self):
        nexttweet = self.next_tweet()
        if nexttweet is None:
            self.response.out.write('')
        else:
            self.response.out.write(nexttweet.tweet)


class LatesttweetHandler(webapp.RequestHandler):
    @classmethod
    def lastest_tweet(cls):
        # latest tweet not kicked(maybe not to be kicked immediately)
        return DB.all().filter('kicked', False).filter('failed', False).order('-tweet_id').get()

    def get(self):
        lastest = self.lastest_tweet()
        if lastest is None:
            self.response.out.write('')
        else:
            self.response.out.write(lastest.tweet)


class LasttweetHandler(webapp.RequestHandler):
    @classmethod
    def last_tweet(cls):
        # last tweet which has been tried to kick(maybe kicked or failed or both)
        # it's not easy to get !(failed == False and kicked == False),
        # so the tweet whose id is smaller than the next tweet is what we want.
        next_ = NexttweetHandler.next_tweet()
        if next_ is None:  # not tweets in the kick peeding
            return DB.all().order('-tweet_id').get()
        else:
            return DB.all().filter('tweet_id <', next_.tweet_id).order('-tweet_id').get()

    def get(self):
        last = self.last_tweet()
        if last is None:
            self.response.out.write('')
        else:
            self.response.out.write('Failed: %s <|> ' % last.failed)
            self.response.out.write('Kicked: %s <|> ' % last.kicked)
            self.response.out.write(last.tweet)

class FetchtweetsHandler(webapp.RequestHandler):
    @classmethod
    def expand(self, url):
        """Expand short url to its original."""

        try:
            res = urlfetch.fetch(url=url, method="HEAD", follow_redirects=False, deadline=10)
        except Exception, e:
            logging.error("expand url %s failed\nerror:%s" % (url, e))
            # there will still be an chance to expand the url when kicks,
            # so leave a flag here.
            return None

        return res.headers['Location'] if res.status_code in (301, 302) else url

    def expand_tco_urls(self, tcos):
        """Return pairs of t.co short urls and their expanded urls.
        If the expanded url is in the malice urls list of Weibo.com, expand
        it to the unshorten one.

        < [{"url":"http://t.co/xxx", "expanded_url":"http://loooooongurl"},
           {"url":"http://t.co/yyy", "expanded_url":"http://goo.gl/abc"}]

        > (["http://t.co/xxx", "http://t.co/yyy"],
           ["http://loooooongurl", "http://zzzzzzzzzzzzzz"])

        Twitter forces to wrap the url longer than 20 characters regardless of
        whether the url was already shortened by other services like goo.gl or
        bit.ly.

        Support expanding short urls see INNOCENT_NETLOCS
        """
        from urlparse import urlsplit
        global INNOCENT_NETLOCS

        tco_urls = []
        tco_expanded_urls = []
        for e in tcos:
            if e["url"] and e["expanded_url"]:  # some urls not expanded(null)
                tco_urls.append(e["url"])
                u = e["expanded_url"]
                o = urlsplit(u)
                uu = self.expand(u) if o.netloc in INNOCENT_NETLOCS else u
                tco_expanded_urls.append(uu)

        return (tco_urls, tco_expanded_urls)

    # fetch user's tweets which are not reply to anyone
    def fetch(self, sid):
        global USER_ID, TWITTER_REQAPI
        # Twitter limits GAE quite a lot, but other proxy api is supported,
        # see README
        req = TWITTER_REQAPI % (USER_ID, sid)

        try:
            res = urlfetch.fetch(url=req, deadline=10)
        except Exception, e:
            logging.error("fetch: %s" % e)
            return

        if res.status_code != 200:
            msg = "twitter api maybe request too much.\n%s" % res.content
            logging.info(msg)
            self.response.out.write(msg)
            return
        else:
            logging.info("fetchtweets: we got something.")

        tweets = json.loads(res.content)
        for t in tweets:
            tweet = t["text"].strip()
            # skip the reply tweet
            if t["in_reply_to_status_id"] is not None:
                continue
            # you can use '@ bla bla bla...' to force not to kick
            if tweet[0] == '@' and tweet[1] in ' abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789':
                continue
            # store the t.co like(goo.gl/bit.ly) urls and their expanded urls
            # 'include_entities' should be turned on in the api calling
            # "entities":{"urls":["url":"", "expanded_url":""]}
            tco_urls, tco_expanded_urls = self.expand_tco_urls(t["entities"]["urls"])

            try:
                DB(tweet_id=t["id"], tweet=t["text"], user_id=USER_ID,
                   tco_urls=tco_urls, tco_expanded_urls=tco_expanded_urls).put()
            except Exception, e:
                logging.error("save entity error: %s" % e)
                break
            #self.response.out.write("tid: %d, t: %s, uid: %d" % (t["id"], t["text"], t["user"]["id"]))
        # end for

    def get_since_id(self):
        if DB.all().count(1) == 0:  # it's really no any tweet fetched
            return 12345678         # this tweet is quite early, so...

        k = DB.all().order('-tweet_id').get()
        return k.tweet_id

    def get(self):
        since_id = self.get_since_id()
        self.fetch(since_id)
#        self.response.out.write(since_id)


class KickassHandler(webapp.RequestHandler):
    def hide_id(self, tweet, instead_str):
        """Replace "@username" in the tweet with other string like "@nil".

        Name user id will have same postfix, like:
        "Hej @blabla RT @blabla: hello @yadayada"  =>
        "Hej @nil_0 RT @nil_0: hello @nil_1"

        If instead_str is empty, every "@xxx" will be removed.
        """

        ids = re.findall('@\w+', tweet)
        if ids:
            ids = list(set(ids))
            for i in range(len(ids)):
                tweet = tweet.replace(ids[i],
                                      instead_str + "_%d" % i if instead_str else "")

        return tweet

    def unwrap_tco(self, t, tco_pairs):
        """Replace t.co urls in the tweet with the expanded ones.

        Twitter forces to wrap all urls using t.co, but t.co urls are blocked by weibo.com.

        parameter t is a datastore entity.

        tco_pairs = [(u'http://t.co/AbC123', u'http://looooongurl.com/blabla')]
        """

        tweet = t.tweet
        for tco_url, tco_expanded_url in tco_pairs:
            # yet another chance to expand the short url not succeeded last time
            if tco_expanded_url is None:
                u = FetchtweetsHandler.expand(tco_url)
                if u is None:
                    tco_expanded_url = tco_url
                else:
                    t.tco_expanded_urls[t.tco_urls.index(tco_url)] = u
                    t.put()
            tweet = tweet.replace(tco_url, tco_expanded_url)
        return tweet

    def get_weibo_count(self, msg):
        global WEIBO_GSID, WEIBO_HOME_URL, WEIBO_HOME_URL_DEADLINE, UA

        headers = {'User-Agent': UA}
        try:
            res = urlfetch.fetch(url=WEIBO_HOME_URL, headers=headers,
                                 deadline=WEIBO_HOME_URL_DEADLINE)
        except Exception, e:
            raise Exception(e)  # just exit...

        # match WEIBO_GSID">微博[362]</a>
        patt = r'%s">微博\[(\d+)\]</a>' % WEIBO_GSID
        v = re.search(patt, res.content)
        try:
            count = int(v.group(1))
        except Exception, e:
            logging.error("get count(%s) error: \n%s\n%s" % (msg, WEIBO_HOME_URL, res.content))
            raise Exception(e)

        return count

    def get(self):
        global TRIED_TIMES_MAX, WEIBO_KICK_URL, WEIBO_KICK_URL_DEADLINE, UA, \
               PLZ_HIDE_ID, PLZ_HIDE_ID_INSTEAD

        t = NexttweetHandler.next_tweet()
        if t is None:
            self.response.out.write("No tweet to kick.")
            return

        if t.tried_times > TRIED_TIMES_MAX:
            t.failed = True
            t.put()
            logging.info("Set failed: %s" % t.tweet)
            return

        headers = {'Content-Type': 'application/x-www-form-urlencoded',
                   'User-Agent': UA}
        t_expanded = self.unwrap_tco(t, zip(t.tco_urls, t.tco_expanded_urls))
        t_expanded = self.hide_id(t_expanded, PLZ_HIDE_ID_INSTEAD) if PLZ_HIDE_ID else t_expanded
        form_fields = {'act': 'add', 'rl': '0', 'content': t_expanded.encode('utf-8')}
        form_data = urllib.urlencode(form_fields)

        count_before_kick = self.get_weibo_count('before')
        try:
            result = urlfetch.fetch(url=WEIBO_KICK_URL, payload=form_data,
                                    method=urlfetch.POST, headers=headers,
                                    deadline=WEIBO_KICK_URL_DEADLINE)
        except Exception, e:
            logging.error("kick: %s\n%s" % (e, t_expanded))
            self.response.out.write("%s" % "kick: urlfetch failed...")
            return

        msg = "Kicked!"  # kicked, but maybe not posted
        t.tried_times += 1
        t.put()

        if result.content.find("发布成功!") == -1:  # not find it
            count_after_kick = self.get_weibo_count('after')
            # count incr 1 if really kicked, but of course there is still
            # a probability that count is increased by user posting a tweet
            # from the weibo page during our kicking procedure.
            if count_before_kick < count_after_kick:
                t.kicked = True
                msg += "(%d:%d) Count increased, maybe OK." % (count_before_kick, count_after_kick)
            else:
                t.whyfailed = result.content.decode("utf-8")
                msg += "(%d:%d) Mostly maybe blocked or censored! Try less." % (count_before_kick, count_after_kick)
                t.tried_times += 1  # mostly blocked, no need try TRIED_TIMES_MAX times
        else:
            t.kicked = True
            msg += " Mostly succeeded."

        logging.info(msg + "\n%s" % t_expanded)
        t.put()


application = webapp.WSGIApplication([('/kickass', KickassHandler),
                                      ('/nexttweet', NexttweetHandler),
                                      ('/latesttweet', LatesttweetHandler),
                                      ('/lasttweet', LasttweetHandler),
                                      ('/fetchtweets', FetchtweetsHandler),
                                      ('/whyfailed/(.*)', WhyfailedPage),
                                      ('/.*', MainPage)], debug=True)

def main():
    run_wsgi_app(application)

if __name__ == '__main__':
    main()
