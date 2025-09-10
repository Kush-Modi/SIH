#!/usr/bin/env python3
import requests
import json

def test_block_issue():
    url = "http://localhost:8000/inject/block-issue"
    
    # Test data
    data = {
        "block_id": "B1",
        "blocked": True
    }
    
    print(f"Testing block issue endpoint...")
    print(f"URL: {url}")
    print(f"Data: {json.dumps(data, indent=2)}")
    
    try:
        response = requests.post(url, json=data)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.text}")
        
        if response.status_code == 200:
            print("✅ Success!")
        else:
            print("❌ Failed!")
            
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    test_block_issue()
