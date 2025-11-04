
import os
import shutil

# Copy the updated file to the server
try:
    # Read the updated file
    with open('widgets/add_bgp_route_dialog.py', 'r') as f:
        content = f.read()
    
    # Write to a temporary file for deployment
    with open('add_bgp_route_dialog_updated.py', 'w') as f:
        f.write(content)
    
    print('File prepared for deployment')
except Exception as e:
    print(f'Error: {e}')
