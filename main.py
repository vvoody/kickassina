# -*- coding: utf-8 -*-
import urllib
import logging
from google.appengine.ext import db
from google.appengine.ext import webapp
from google.appengine.api import urlfetch
from django.utils import simplejson as json
from google.appengine.ext.webapp.util import run_wsgi_app

# 'README' will tell you how to fill the below 3 values
USER_ID=None  # user_id is immutable, better than screen_name
WEIBO_GSID=None
WEIBO_ST=None
### DO NOT CHANGE THE FOLLOWING VALUES UNLESS YOU ARE SURE ###
WEIBO_KICK_URL='http://t.sina.cn/dpool/ttt/mblogDeal.php?st=%s&st=%s&gsid=%s' % (WEIBO_ST, WEIBO_ST, WEIBO_GSID)
WEIBO_HOME_URL='http://t.sina.cn/dpool/ttt/home.php?vt=1&gsid=%s' % WEIBO_GSID
WEIBO_KICK_URL_DEADLINE=30
WEIBO_HOME_URL_DEADLINE=12
TRIED_TIMES_MAX=5
###


class DB(db.Model):
    kicked = db.BooleanProperty(default=False)
    tweet_id = db.IntegerProperty()
    tweet = db.StringProperty(multiline=True)
    user_id = db.IntegerProperty()
    failed = db.BooleanProperty(default=False)    # bloody damn censorship or sina's block
    whyfailed = db.TextProperty()
    tried_times = db.IntegerProperty(default=0)


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
    # fetch user's tweets which are not reply to anyone
    def fetch(self, sid):
        global USER_ID
        req = 'http://api.twitter.com/1/statuses/user_timeline.json?user_id=%d&trim_user=true&include_rts=true&since_id=%d' % (USER_ID, sid)
        try:
            res = urlfetch.fetch(url=req, deadline=10)
        except Exception, e:
            logging.error("fetch: %s" % e)
            return

        if res.status_code != 200:
            msg = "twitter api maybe request too much."
            logging.info(msg)
            self.response.out.write(msg)
            return

        tweets = json.loads(res.content)
        for t in tweets:
            tweet = t["text"].strip()
            # skip the reply tweet
            if t["in_reply_to_status_id"] is not None:
                continue
            # you can use '@ bla bla bla...' to force not to kick
            if tweet[0] == '@' and tweet[1] in ' abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789':
                continue

            DB(tweet_id=t["id"], tweet=t["text"], user_id=USER_ID).put()
            #self.response.out.write("tid: %d, t: %s, uid: %d" % (t["id"], t["text"], t["user"]["id"]))

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
    def get_weibo_count(self):
        global WEIBO_GSID, WEIBO_HOME_URL, WEIBO_HOME_URL_DEADLINE
        try:
            res = urlfetch.fetch(url=WEIBO_HOME_URL, deadline=WEIBO_HOME_URL_DEADLINE)
        except Exception, e:
            raise Exception(e)  # just exit...

        import re
        # match WEIBO_GSID">微博[362]</a>
        patt = r'%s">微博\[(\d+)\]</a>' % WEIBO_GSID
        v = re.search(patt, res.content)
        return int(v.group(1))

    def get(self):
        global TRIED_TIMES_MAX, WEIBO_KICK_URL, WEIBO_KICK_URL_DEADLINE

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
                   'Useg-Agent': 'Opera/9.80 (Android; Linux; Opera Mobi/ADR-1011151731; U; en) Presto/2.5.28 Version/10.1'}
        form_fields = {'act': 'add', 'rl': '0', 'content': t.tweet.encode('utf-8')}
        form_data = urllib.urlencode(form_fields)

        count_before_kick = self.get_weibo_count()
        try:
            result = urlfetch.fetch(url=WEIBO_KICK_URL, payload=form_data,
                                    method=urlfetch.POST, headers=headers,
                                    deadline=WEIBO_KICK_URL_DEADLINE)
        except Exception, e:
            logging.error("kick: %s\n%s" % (e, t.tweet))
            self.response.out.write("%s" % "kick: urlfetch failed...")
            return

        msg = "Kicked!"  # kicked, but maybe not posted

        if result.content.find("发布成功!") == -1:  # not find it
            count_after_kick = self.get_weibo_count()
            # count incr 1 if really kicked, but of course there is still
            # a probability that count is increased by user posting a tweet
            # from the weibo page during our kicking procedure.
            if count_before_kick < count_after_kick:
                t.kicked = True
                msg += " Count increased, maybe OK."
            else:
                t.whyfailed = result.content.decode("utf-8")
                msg += " But maybe blocked or censored! Try again next time."
        else:
            t.kicked = True
            msg += " Mostly succeeded."

        logging.info(msg + "\n%s" % t.tweet)
        t.tried_times += 1
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
