import asyncio
import json
import urllib.request
import websockets

async def main():
    req = urllib.request.Request("http://127.0.0.1:9229/json")
    try:
        res = urllib.request.urlopen(req)
        targets = json.loads(res.read())
    except Exception as e:
        print("Failed to connect:", e)
        return
        
    for t in targets:
        if t.get('type') in ['page', 'iframe'] and 'Antigravity' in t.get('title', ''):
            ws_url = t.get('webSocketDebuggerUrl')
            if not ws_url: continue
            print(f"Target: {t.get('title')}")
            
            try:
                async with websockets.connect(ws_url, max_size=10_000_000) as ws:
                    await ws.send(json.dumps({"id": 1, "method": "Accessibility.getFullAXTree"}))
                    while True:
                        msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                        data = json.loads(msg)
                        if data.get('id') == 1:
                            nodes = data.get('result', {}).get('nodes', [])
                            names = []
                            for n in nodes:
                                name = n.get('name', {}).get('value', '')
                                role = n.get('role', {}).get('value', '')
                                if name:
                                    names.append(f"[{role}] {name}")
                            with open('scratch/axtree.txt', 'w', encoding='utf-8') as f:
                                f.write('\n'.join(names))
                            print("Dumped axtree.txt with", len(names), "nodes")
                            break
            except Exception as e:
                print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
