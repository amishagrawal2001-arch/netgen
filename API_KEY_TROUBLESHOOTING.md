# API Key Troubleshooting Guide

## Issue
API key is configured in settings but test plan generation still fails with "Cloud-only mode is enabled but no API key is configured."

## Enhanced Logging

The server now logs detailed information about API key status:

### Server Logs to Check

When you try to generate a test plan, look for these log messages:

```
[AI TEST PLAN] API Key Status: present=True/False, length=XX, preview=sk-...
[AI TEST PLAN] API Base: https://api.groq.com/openai/v1 or (default - OpenAI)
[AI TEST PLAN] AI Mode Preference: cloud, cloud_only=True, local_only=False
[AI SETTINGS] API key found: length=XX, source=ai_settings/environment
```

### Debugging Steps

1. **Check if API key is saved:**
   - Look for: `[AI SETTINGS] API key updated from client`
   - This confirms the key was received by the server

2. **Check if API key is retrieved:**
   - Look for: `[AI SETTINGS] API key found: length=XX`
   - If you see: `[AI SETTINGS] API key not found` → Key is not being retrieved

3. **Check API key source:**
   - `source=ai_settings` → Key is in memory
   - `source=environment` → Key is in environment variable
   - If neither → Key needs to be reloaded

## Common Issues

### Issue 1: API Key Not Persisted
**Symptom:** Key works until server restart

**Solution:**
- Check if `/opt/OSTG/.ostg_ai_server_settings.env` exists
- Check file permissions (should be 600)
- Verify file contains: `export OPENAI_API_KEY='your-key'`

### Issue 2: API Key Not Loaded on Startup
**Symptom:** Key is in file but not loaded

**Solution:**
- Server should auto-reload from file
- Check logs for: `[AI SETTINGS] Reloaded API key from file`
- If missing, key format might be wrong

### Issue 3: API Key Empty or Invalid
**Symptom:** Key is present but API calls fail

**Solution:**
- Check key length (should be > 20 characters)
- Verify key starts with `sk-` (OpenAI) or appropriate prefix
- Test key manually: `curl https://api.openai.com/v1/models -H "Authorization: Bearer YOUR_KEY"`

## Manual Verification

### Check Server Settings
```bash
# SSH to server
ssh root@san-rt-ai-srv01

# Check if settings file exists
cat /opt/OSTG/.ostg_ai_server_settings.env

# Check environment variable
echo $OPENAI_API_KEY

# Check server logs
tail -f /var/log/ostg/server.log | grep "AI SETTINGS\|AI TEST PLAN"
```

### Test API Key Endpoint
```bash
# Check if API key is set
curl http://san-rt-ai-srv01:5000/api/ai/settings

# Should return:
# {
#   "has_api_key": true,
#   "has_api_base": true,
#   ...
# }
```

## Quick Fix

If API key is configured but not detected:

1. **Restart the server** to reload settings
2. **Re-save the API key** via UI to ensure it's persisted
3. **Check server logs** for the debug messages above

## Next Steps

After applying the fix:
1. Restart the server
2. Try generating a test plan
3. Check server logs for the new debug messages
4. Share the log output if issue persists

