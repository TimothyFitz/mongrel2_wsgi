#!/usr/bin/python
# -*- coding: utf-8 -*-

import os, sys
import urllib, httplib
from urlparse import urlparse
from uuid import uuid4

import json

from wsgiref.handlers import SimpleHandler
try:
    from cStringIO import StringIO
except:
    from StringIO import StringIO

DEBUG = False

def random_uuid():
    return str(uuid4())
    
def read_status(line):
    try:
        version, status, reason = line.split(None, 2)
    except ValueError:
        try:
            version, status = line.split(None, 1)
            reason = ""
        except ValueError:
            version, status, reason = line, "", ""
    return version, status, reason
    
def add_http_variables(env, headers):
    for k,v in headers.items():
        k=k.replace('-','_').upper(); v=v.strip()
        if k in env:
            continue                    # skip content length, type,etc.
        http_k = 'HTTP_'+k
        if http_k in env:
            env[http_k] += ','+v     # comma-separate multiple headers
        else:
            env[http_k] = v
            
def add_request_metavariables(env, headers):
    # From rfc3875
    if headers.has_key('Content-Length'):
        env['CONTENT_LENGTH'] = headers['Content-Length']

    if headers.has_key('Content-Type'):
        env['CONTENT_TYPE'] = headers['Content-Type']
    
    env['GATEWAY_INTERFACE'] = "CGI/1.1"
    env['SCRIPT_NAME'] = headers['PATTERN'].split('(')[0]
    path = urllib.unquote(headers['PATH'])[len(env['SCRIPT_NAME']):]
    env['PATH_INFO'] = '/' + path if len(path) == 0 or path[0] != '/' else path
    # PATH_TRANSLATED is stupid.
    env['QUERY_STRING'] = urlparse(headers['URI']).query
    env['SERVER_PROTOCOL'] = headers['VERSION']
    env['REMOTE_ADDR'] = headers['X-Forwarded-For']
    # REMOTE_HOST is stupid.
    env['REQUEST_METHOD'] = headers['METHOD']
    env['SERVER_NAME'], env['SERVER_PORT'] = parse_host(headers['Host'])
    env['SERVER_SOFTWARE'] = 'mongrel2_wsgi'
            
def parse_host(host):
    if ':' in host:
        return host.split(':', 1)
    else:
        return host, '80'

def wsgi_server(application, conn):
    '''WSGI handler based on the Python wsgiref SimpleHandler.
    
    A WSGI application should return a iterable op StringTypes. 
    Any encoding must be handled by the WSGI application itself.
    '''
    
    # TODO - this wsgi handler executes the application and renders a page 
    # in memory completely before returning it as a response to the client. 
    # Thus, it does not "stream" the result back to the client. It should be 
    # possible though. The SimpleHandler accepts file-like stream objects. So, 
    # it should be just a matter of connecting 0MQ requests/response streams to 
    # the SimpleHandler requests and response streams. However, the Python API 
    # for Mongrel2 doesn't seem to support file-like stream objects for requests 
    # and responses. Unless I have missed something.
    
    # setup connection handler
    # sender_id is automatically generated 
    # so that each handler instance is uniquely identified

    while True:
        if DEBUG: print "WAITING FOR REQUEST"
        
        # receive a request
        req = conn.recv()
        if DEBUG: 
            print "REQUEST HEADERS: %r\n" % req.headers
            print "REQUEST BODY: %r\n" % req.body
        
        if req.is_disconnect():
            if DEBUG: print "DISCONNECT"
            continue #effectively ignore the disconnect from the client
        
        # json parsing gives us unicode instead of ascii.
        headers = dict((key.encode('ascii'), value.encode('ascii')) for (key,value) in req.headers.items())
        
        env = {}
        
        add_request_metavariables(env, headers)
        add_http_variables(env, headers)
        
        if DEBUG: print "ENVIRON: %r\n" % env
        
        # SimpleHandler needs file-like stream objects for
        # requests, errors and reponses
        reqIO = StringIO(req.body)
        errIO = StringIO()
        respIO = StringIO()
        
        # execute the application
        simple_handler = SimpleHandler(reqIO, respIO, errIO, env, multithread = False, multiprocess = False)
        simple_handler.run(application)
        
        response = respIO.getvalue()
        errors = errIO.getvalue()

        # return the response
        if DEBUG: print "RESPONSE: %r\n" % response
        if errors:
            if DEBUG: print "ERRORS: %r" % errors
        
        conn.reply(req, response)
        
        # Check if we should close the connection.
        
        respIO.seek(0)
        
        protocol = respIO.readline()[:8]
        msg = httplib.HTTPMessage(respIO, 0)
        http_1p1 = protocol == 'HTTP/1.1'
        
        conn_close = http_1p1 and msg.getheader("Connection") == "close"
        keep_alive = http_1p1 or msg.getheader("Connection") == "Keep-Alive"
        if conn_close or not keep_alive:
            if DEBUG: print "EXPLICITLY CLOSING CONNECTION"
            conn.reply(req, "")
