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


class MainPage(webapp.RequestHandler):
    def get(self):
        self.response.out.write("fair enough")


class LasttweetHandler(webapp.RequestHandler):
    @classmethod
    def last_tweet(cls):
        # 'lasttweet' here doesn't mean the latest tweet,
        # it's the next tweet to kick
        return DB.all().filter('kicked', False).filter('failed', False).order('-tweet_id').get()

    def get(self):
        lasttweet = self.last_tweet()
        self.response.out.write(lasttweet.tweet)


class FetchtweetsHandler(webapp.RequestHandler):
    # fetch user's tweets which are not reply to anyone
    def fetch(self, sid):
        global USER_ID
        req = 'http://api.twitter.com/1/statuses/user_timeline.json?user_id=%d&trim_user=true&include_rts=true&since_id=%d' % (USER_ID, sid)
        try:
            res = urlfetch.fetch(req)
        except Exception, e:
            logging.error("fetch: %s" % e)
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
            logging.info("No tweet to kick.")
            return

        headers = {'Content-Type': 'application/x-www-form-urlencoded',
                   'Useg-Agent': 'Opera/9.80 (Android; Linux; Opera Mobi/ADR-1011151731; U; en) Presto/2.5.28 Version/10.1'}
        form_fields = {'act': 'add', 'rl': '0', 'content': t.tweet.encode('utf-8')}
        form_data = urllib.urlencode(form_fields)
        try:
            result = urlfetch.fetch(url=REQ_URL,
                                    payload=form_data, method=urlfetch.POST, headers=headers)
        except Exception, e:
            logging.error("urlfetch: %s" % e)
            return

        # tweet may be censored
        if result.content.find("发布成功!") == -1:
            t.failed = True
        t.kicked = True
        t.put()


application = webapp.WSGIApplication([('/kickass', KickassHandler),
                                      ('/lasttweet', LasttweetHandler),
                                      ('/fetchtweets', FetchtweetsHandler),
                                      ('/.*', MainPage)], debug=True)


def main():
    run_wsgi_app(application)

if __name__ == '__main__':
    main()