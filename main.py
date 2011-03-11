# -*- coding: utf-8 -*-
import urllib
import logging
from google.appengine.ext import db
from google.appengine.ext import webapp
from google.appengine.api import urlfetch
from django.utils import simplejson as json
from google.appengine.ext.webapp.util import run_wsgi_app

# hardcode is bad, but whatever...
# replace this with your true user id
# we prefer user_id than screen_name, cuz the former is immutable.
USER_ID=None
# fetch the url after logining to t.sina.cn
REQ_URL=None

class DB(db.Model):
    kicked = db.BooleanProperty(default=False)
    tweet_id = db.IntegerProperty()
    tweet = db.StringProperty()
    user_id = db.IntegerProperty()
    failed = db.BooleanProperty(default=False)    # bloody damn censorship
    whyfailed = db.TextProperty()


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


class LasttweetHandler(webapp.RequestHandler):
    @classmethod
    def last_tweet(cls):
        # 'lasttweet' here doesn't mean the latest tweet,
        # it's the next tweet to kick
        return DB.all().filter('kicked', False).filter('failed', False).order('-tweet_id').get()

    def get(self):
        lasttweet = self.last_tweet()
        if lasttweet is None:
            self.response.out.write('')
        else:
            self.response.out.write(lasttweet.tweet)


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
    def get(self):
        global REQ_URL

        t = LasttweetHandler.last_tweet()
        if t is None:
            self.response.out.write("No tweet to kick.")
            return

        headers = {'Content-Type': 'application/x-www-form-urlencoded',
                   'Useg-Agent': 'Opera/9.80 (Android; Linux; Opera Mobi/ADR-1011151731; U; en) Presto/2.5.28 Version/10.1'}
        form_fields = {'act': 'add', 'rl': '0', 'content': t.tweet.encode('utf-8')}
        form_data = urllib.urlencode(form_fields)
        try:
            result = urlfetch.fetch(url=REQ_URL, payload=form_data,
                                    method=urlfetch.POST, headers=headers,
                                    deadline=10)
        except Exception, e:
            logging.error("kicked: %s\n%s" % (e, t.tweet))
            return

        msg = "Kicked!"

        # tweet may be censored
        if result.content.find("发布成功!") == -1:
            t.failed = True
            t.whyfailed = result.content.decode("utf-8")
            msg += " But maybe censored!"

        logging.info(msg)
        t.kicked = True
        t.put()


application = webapp.WSGIApplication([('/kickass', KickassHandler),
                                      ('/lasttweet', LasttweetHandler),
                                      ('/fetchtweets', FetchtweetsHandler),
                                      ('/whyfailed/(.*)', WhyfailedPage),
                                      ('/.*', MainPage)], debug=True)

def main():
    run_wsgi_app(application)

if __name__ == '__main__':
    main()
