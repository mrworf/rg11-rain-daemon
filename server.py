#!/usr/bin/env python
#
# This file is part of RG-11 Rain Daemon.
#
# multiRemote is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
#
# RG-11 Rain Daemon is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with RG-11 Rain Daemon.  If not, see <http://www.gnu.org/licenses/>.
#
#
import sys
import time
import threading
import argparse
from array import array
import datetime
from flask import Flask, jsonify, abort
import sqlite3

class ChipGPIO:
  def __init__(self):
    self.pins = {}

  def __write__(self, filename, value):
    """ Writes value to filename """
    try:
      with open(filename, "wb", 0) as f:
        f.write(str(value))
    except IOError as e:
      if e.errno is 16:
        print "WARNING: Something else is using GPIO %d" % value
      else:
        print "ERROR: Unable to write to \"%s\" because %d, %s" % (filename, e.errno, e.strerror)

  def __read__(self, filename):
    """ Reads value from filename """
    value = None
    try:
      with open(filename, "rb", 0) as f:
        value = f.read()
      return int(value)
    except:
      print "ERROR: Unable to read \"%s\"" % filename
      print sys.exc_info()[0]

  def alloc(self, pin, output):
    """
    PIN refers to GPIOs XIO-P0 to XIO-P7 and is simply a number from 0 to 7
    OUTPUT indicates that you will be writing to this pin, which sets it to output instead of default input
    If the pin has been init:ed already, this call fails and returns False
    """
    if pin < 0 or pin > 7:
      print "ERROR: Only PIN values of 0 through 7 is allowed, %d is invalid" % pin
      return False
    if pin in self.pins:
      print "ERROR: Pin %d is already in-use" % pin
      return False
    self.pins[pin] = output
    self.__write__("/sys/class/gpio/export", pin + 408)
    if output:
      self.__write__("/sys/class/gpio/gpio%d/direction" % (pin + 408), "out")

  def dealloc(self, pin):
    if pin < 0 or pin > 7:
      print "ERROR: Only PIN values of 0 through 7 is allowed, %d is invalid" % pin
      return False
    if pin not in self.pins:
      print "ERROR: Pin %d is not in-use" % pin
      return False
    if self.pins[pin]:
      self.__write__("/sys/class/gpio/gpio%d/value" % (pin + 408), 0)
    self.__write__("/sys/class/gpio/unexport", pin + 408)

  def read(self, pin):
    if pin < 0 or pin > 7:
      print "ERROR: Only PIN values of 0 through 7 is allowed, %d is invalid" % pin
      return False
    if pin not in self.pins:
      print "ERROR: Pin %d is not in-use" % pin
      return False
    value = self.__read__("/sys/class/gpio/gpio%d/value" % (408 + pin))
    return value == 1

parser = argparse.ArgumentParser(description="RG-11 Rain Daemon - Keeping track of the wetness", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument('--logfile', metavar="FILE", help="Log to file instead of stdout")
parser.add_argument('--port', default=8080, type=int, help="Port to listen on")
parser.add_argument('--listen', metavar="ADDRESS", default="0.0.0.0", help="Address to listen on")
parser.add_argument('--bucketsize', metavar="BUCKET", default=1, type=int, help='Size of the bucket (0 = 0.01", 1 = 0.001", 2 = 0.0001"')
parser.add_argument('--database', metavar="DATABASE", default="rg11.db", help="Where to store rain data")
cmdline = parser.parse_args()

# init some good stuff
#
raindivider = 100
if cmdline.bucketsize == 1:
  raindivider = 1000
if cmdline.bucketsize == 2:
  raindivider = 10000

class rainCollector(threading.Thread):
  def getMinute(self, t):
    # Get the minute
    return int((t / 60)) % 60

  def getHour(self, t):
    # Get the hour
    return int((t / 3600)) % 24

  def getTime(self):
    """ Strips off the seconds """
    return int(time.time()/60)*60

  def currentMinute(self):
    return self.getMinute(self.getTime())

  def currentHour(self):
    return self.getHour(self.getTime())

  def getLastHour(self):
    return self.buckets_1h

  def getLastDay(self):
    return self.buckets_1d

  def __init__(self, dbfile, divider):
    threading.Thread.__init__(self)
    self.daemon = True
    self.divider = divider
    self.buckets_1h = array('i')
    self.buckets_1d = array('i')
    self.dbfile = dbfile
    for b in range(0, 60):
      self.buckets_1h.append(0)
    for b in range(0, 24):
      self.buckets_1d.append(0)

  def run(self):
    db = sqlite3.connect(self.dbfile)

    # Setup the DB
    db.execute('''CREATE TABLE IF NOT EXISTS RAIN
       (TS INT PRIMARY KEY     NOT NULL,
        AMOUNT         INT     NOT NULL,
        DIVIDER        INT     NOT NULL);''')

    gpio = ChipGPIO()
    gpio.alloc(1, False)

    lastval = 0
    t = self.getTime()
    lastmin = self.getMinute(t)

    # Load anything which happened in the last 60min
    result = db.execute('''SELECT * FROM RAIN WHERE TS > %d''' % (t - 3600))
    for row in result:
      print repr(row)
      m = self.getMinute(row[0])
      self.buckets_1h[m] = row[1]
      if self.divider != row[2]:
        print "WARNING: Divider has changed, not supported yet when pulling up old data"

    # Load the hourly summary
    result = db.execute('''SELECT TS,SUM(AMOUNT),((TS/3600)%%24) AS HOUR FROM RAIN WHERE TS > %d GROUP BY HOUR''' % (t - (24*3600)))
    for row in result:
      print repr(row)
      m = row[2]
      self.buckets_1d[m] = row[1]

    print "rainCollector running"
    while True:
      ti = time.time()
      t = self.getTime()
      m = self.getMinute(t)
      v = gpio.read(1)

      if lastmin != m:
        if self.buckets_1h[lastmin] > 0:
          try:
            db.execute('INSERT INTO RAIN (TS,AMOUNT,DIVIDER) VALUES (%d,%d,%d)' % (t-60,self.buckets_1h[lastmin],self.divider))
            db.commit()
            print 'Stored bucket %d with %f" of water' % (t-60, float(self.buckets_1h[lastmin]) / self.divider)
          except sqlite3.OperationalError as e:
            print "ERROR: Unable to save bucket (typically happens if there is storage issues or time has rolled back)"
            print e
        # Reset this bucket
        self.buckets_1h[m] = 0

      if lastval != v:
        print "PIN has changed to %d" % v
      if lastval != v and lastval == 1: # Count on fall
        print 'Rain! %f"' % (1/self.divider)
        self.buckets_1h[m] += 1

      # Summarize the last hour
      i = 0
      for x in range(0, m):
        i += self.buckets_1h[x]
      self.buckets_1d[self.getHour(t)] = i

      lastval = v
      lastmin = m

      ti = time.time() - ti;
      if ti > 0.025:
        print "Time spent processing: %f ms" % ti
      ti = time.time();
      time.sleep(0.025) # RG11 triggers for 50ms, so sleep half that so we don't miss it
      ti = time.time() - ti
      if ti > 0.049:
        print "WARNING: Time elapsed during sleep 25ms was %f ms, might miss rain interrupt" % ti

# Create the REST interface
app = Flask(__name__)

@app.route("/lastrain")
def api_lastrain():
  sql = '''SELECT ts FROM rain ORDER BY ts DESC LIMIT 1'''
  msg = {"error" : "No rain recorded"}

  db = sqlite3.connect(cmdline.database)
  result = db.execute(sql)
  for entry in result:
    msg = {"timestamp": entry[0]}
  json = jsonify(msg)
  json.status_code = 200
  db.close()    
  return json

@app.route("/archive/<type>")
def api_archive(type):
  sql = None

  if type == "thismonth":
    sql = '''SELECT STRFTIME("%d%H", DATETIME(ts, 'unixepoch')) AS dayhour, SUM(amount) AS rain 
             FROM rain 
             WHERE ts >= STRFTIME("%s", DATE('now', 'start of month'))
             GROUP BY dayhour 
             ORDER BY dayhour'''
  elif type == "lastmonth":
    sql = '''SELECT STRFTIME("%d%H", DATETIME(ts, 'unixepoch')) AS dayhour, SUM(amount) AS rain 
             FROM rain 
             WHERE ts >= STRFTIME("%s", DATE('now', 'start of month', '-1 month'))
             GROUP BY dayhour 
             ORDER BY dayhour'''
  elif type == "thisyear":
    sql = '''SELECT STRFTIME("%j", DATETIME(ts, 'unixepoch')) AS day, SUM(amount) AS rain 
             FROM rain 
             WHERE ts >= STRFTIME("%s", DATE('now', 'start of year'))
             GROUP BY day
             ORDER BY day'''

  if sql is not None:
    db = sqlite3.connect(cmdline.database)
    result = db.execute(sql)
    msg = {"divider" : raindivider, "entry" : {}}
    for entry in result:
      msg["entry"][entry[0]] = entry[1]
    json = jsonify(msg)
    json.status_code = 200
    db.close()    
    return json
  else:
    abort(404)

@app.route("/present")
def html_display():
  result = ""
  with open("present.html") as f:
    result = f.read()
  return result

@app.route("/")
def api_root():
  bucket = collector.currentMinute()
  ts = datetime.datetime.utcnow()
  ts = ts.replace(second=0, microsecond=0, minute=bucket)
  msg = {
    "current" : ts.isoformat(),
    "divider" : raindivider,
    "hour": [],
    "day": []
  }
  # Iterate through the last hour
  buckets = collector.getLastHour()
  for x in range(0, 60):
    msg["hour"].append(buckets[(60 - x + bucket) % 60])
  bucket = collector.currentHour()
  buckets = collector.getLastDay()
  for x in range(0, 24):
    msg["day"].append(buckets[(24 - x + bucket) % 24])

  json = jsonify(msg)
  json.status_code = 200
  return json


collector = rainCollector(cmdline.database, raindivider)
collector.start()

if __name__ == "__main__":
  app.debug = False
  app.run(host=cmdline.listen, port=cmdline.port)
