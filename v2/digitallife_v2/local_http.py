"""Small cancellable HTTP client for local inference and client bridges only."""
import asyncio
import json
from contextlib import suppress
from urllib.parse import urlsplit
from .safety import Blocked


def endpoint(value):
    try:
        url=urlsplit(value)
        if (url.scheme!='http' or url.hostname not in {'127.0.0.1','::1'}
                or url.username or url.password or url.query or url.fragment
                or url.path not in {'','/'} or not url.port):raise ValueError()
        return url.hostname,url.port
    except (ValueError,TypeError,AttributeError):raise Blocked('LOCAL_HTTP_ENDPOINT_INVALID') from None


async def request(base,path,payload,*,token='',timeout=30,max_bytes=1_000_000):
    """No proxies, redirects or retries; cancellation closes the connection."""
    host,port=endpoint(base)
    if not path.startswith('/') or any(c in path+token for c in '\r\n'):
        raise Blocked('LOCAL_HTTP_REQUEST_INVALID')
    data=json.dumps(payload,ensure_ascii=False).encode('utf-8')
    async def exchange():
        reader,writer=await asyncio.open_connection(host,port,limit=65536)
        try:
            authority=f'[{host}]:{port}' if ':' in host else f'{host}:{port}'
            headers=f'POST {path} HTTP/1.1\r\nHost: {authority}\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: {len(data)}\r\n'
            if token:headers+=f'Authorization: Bearer {token}\r\n'
            writer.write(headers.encode('ascii')+b'\r\n'+data);await writer.drain()
            raw=await reader.readuntil(b'\r\n\r\n')
            if len(raw)>32768:raise Blocked('LOCAL_HTTP_HEADERS_LIMIT')
            lines=raw.decode('latin1').split('\r\n');status=int(lines[0].split()[1])
            if status!=200:raise Blocked('LOCAL_HTTP_REQUEST_REFUSED')
            fields={k.strip().lower():v.strip() for line in lines[1:] if ':' in line for k,v in [line.split(':',1)]}
            transfer=fields.get('transfer-encoding','').lower()
            if transfer=='chunked':
                output=bytearray()
                while True:
                    size=int((await reader.readuntil(b'\r\n')).split(b';',1)[0],16)
                    if size<0 or len(output)+size>max_bytes:raise Blocked('LOCAL_HTTP_RESPONSE_LIMIT')
                    if size==0:break
                    output.extend(await reader.readexactly(size))
                    if await reader.readexactly(2)!=b'\r\n':raise ValueError()
                body=bytes(output)
            elif transfer:raise Blocked('LOCAL_HTTP_TRANSFER_UNSUPPORTED')
            elif 'content-length' in fields:
                size=int(fields['content-length'])
                if not 0<size<=max_bytes:raise Blocked('LOCAL_HTTP_RESPONSE_LIMIT')
                body=await reader.readexactly(size)
            else:
                output=bytearray()
                while True:
                    part=await reader.read(min(65536,max_bytes+1-len(output)))
                    if not part:break
                    output.extend(part)
                    if len(output)>max_bytes:raise Blocked('LOCAL_HTTP_RESPONSE_LIMIT')
                body=bytes(output)
            return body,fields.get('content-type','')
        finally:
            writer.close()
            await writer.wait_closed()
    try:return await asyncio.wait_for(exchange(),timeout)
    except (Blocked,asyncio.TimeoutError):raise
    except (OSError,ValueError,IndexError,EOFError,asyncio.IncompleteReadError,asyncio.LimitOverrunError) as error:
        raise Blocked('LOCAL_HTTP_EXCHANGE_FAILED') from error


async def json_request(base,path,payload,**options):
    raw,_=await request(base,path,payload,**options)
    try:return json.loads(raw)
    except (ValueError,UnicodeError):raise Blocked('LOCAL_HTTP_JSON_INVALID') from None


async def events(base,token,ready,*,idle_seconds=None,max_event_bytes=131072):
    """Authenticated SSE from the separate local QQ event server; no redirects/replay."""
    host,port=endpoint(base)
    if any(c in token for c in '\r\n') or not token.isascii():raise Blocked('LOCAL_HTTP_REQUEST_INVALID')
    reader,writer=await asyncio.wait_for(asyncio.open_connection(host,port,limit=max_event_bytes+65536),10)
    try:
        authority=f'[{host}]:{port}' if ':' in host else f'{host}:{port}'
        writer.write((f'GET /_events HTTP/1.1\r\nHost: {authority}\r\nAuthorization: Bearer {token}\r\nAccept: text/event-stream\r\nConnection: close\r\n\r\n').encode('ascii'))
        await writer.drain()
        raw=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),10)
        lines=raw.decode('latin1').split('\r\n')
        fields={k.strip().lower():v.strip().lower() for line in lines[1:] if ':' in line for k,v in [line.split(':',1)]}
        if len(raw)>32768 or int(lines[0].split()[1])!=200 or 'text/event-stream' not in fields.get('content-type',''):
            raise Blocked('LOCAL_SSE_REFUSED')
        transfer=fields.get('transfer-encoding','')
        if transfer not in {'','chunked'}:raise Blocked('LOCAL_HTTP_TRANSFER_UNSUPPORTED')
        ready.set();buffer=bytearray();frame=[];frame_size=0
        while True:
            if transfer=='chunked':
                size=int((await asyncio.wait_for(reader.readuntil(b'\r\n'),idle_seconds)).split(b';',1)[0],16)
                if size==0:return
                if not 0<size<=max_event_bytes:raise Blocked('LOCAL_SSE_EVENT_LIMIT')
                part=await asyncio.wait_for(reader.readexactly(size),idle_seconds)
                if await reader.readexactly(2)!=b'\r\n':raise Blocked('LOCAL_SSE_CHUNK_INVALID')
            else:
                part=await asyncio.wait_for(reader.read(8192),idle_seconds)
                if not part:return
            buffer.extend(part)
            while b'\n' in buffer:
                raw_line,_,remaining=buffer.partition(b'\n');buffer=bytearray(remaining)
                line=raw_line.rstrip(b'\r')
                if line.startswith(b'data:'):
                    frame.append(line[5:].lstrip(b' '));frame_size+=len(line)
                elif not line and frame:
                    try:value=json.loads(b'\n'.join(frame))
                    except (ValueError,UnicodeError):raise Blocked('LOCAL_SSE_JSON_INVALID') from None
                    frame=[];frame_size=0
                    if isinstance(value,dict):yield value
                if frame_size+len(buffer)>max_event_bytes:raise Blocked('LOCAL_SSE_EVENT_LIMIT')
            if frame_size+len(buffer)>max_event_bytes:raise Blocked('LOCAL_SSE_EVENT_LIMIT')
    finally:
        writer.close()
        with suppress(OSError):await writer.wait_closed()
