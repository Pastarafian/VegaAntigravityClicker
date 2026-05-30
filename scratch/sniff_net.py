import asyncio
import json
import urllib.request
import websockets

async def main():
    req = urllib.request.Request("http://127.0.0.1:9229/json")
    res = urllib.request.urlopen(req)
    targets = json.loads(res.read())
    
    for t in targets:
        if t.get('type') in ['page', 'iframe']:
            ws_url = t.get('webSocketDebuggerUrl')
            if not ws_url: continue
            print(f"Target: {t.get('title')} ({ws_url})")
            
            try:
                async with websockets.connect(ws_url, max_size=10_000_000) as ws:
                    await ws.send(json.dumps({"id": 1, "method": "Network.enable"}))
                    print(f"Enabled network for {ws_url}. Waiting for 2 seconds...")
                    
                    # Also try to dump the DOM to see if the quota is there
                    # Let's wait a bit to collect network events
                    start_time = asyncio.get_event_loop().time()
                    while asyncio.get_event_loop().time() - start_time < 2:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
                            data = json.loads(msg)
                            if data.get('method') == 'Network.requestWillBeSent':
                                req_url = data['params']['request']['url']
                                if 'http' in req_url:
                                    print(f"REQ: {req_url}")
                            elif data.get('method') == 'Network.responseReceived':
                                resp_url = data['params']['response']['url']
                                if 'http' in resp_url:
                                    print(f"RESP: {resp_url}")
                        except asyncio.TimeoutError:
                            pass
            except Exception as e:
                print(f"Error connecting: {e}")

if __name__ == "__main__":
    asyncio.run(main())
