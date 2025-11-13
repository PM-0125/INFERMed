# How to Restore the Hidden Settings Sidebar

If your Streamlit sidebar is hidden and you can't see the settings, here are **reliable methods** to restore it:

## Method 1: Browser Console (Most Reliable) ⭐

1. **Open your browser's Developer Tools**:
   - Press `F12` (or `Ctrl+Shift+I` on Windows/Linux, `Cmd+Option+I` on Mac)
   - Or right-click anywhere on the page → "Inspect" / "Inspect Element"

2. **Go to the Console tab**

3. **Paste and run this code**:
   ```javascript
   document.querySelector('[data-testid="stSidebarCollapseButton"]')?.click();
   ```

4. The sidebar should appear immediately!

## Method 2: Clear Browser Storage

1. **Open Developer Tools** (F12)
2. **Go to the "Application" tab** (Chrome) or "Storage" tab (Firefox)
3. **Click on "Local Storage"** in the left sidebar
4. **Find your Streamlit URL** (usually `http://localhost:8501` or similar)
5. **Delete these keys** if they exist:
   - `sidebarState`
   - `sidebarCollapsed`
   - Any key containing "sidebar"
6. **Refresh the page** (F5 or Ctrl+R)

## Method 3: Look for the Toggle Button

1. Look at the **top-left corner** of the page
2. You should see a small button (usually `>` or `☰` icon)
3. Click it to toggle the sidebar

## Method 4: Keyboard Shortcut

Some browsers/Streamlit versions support:
- Press `A` key (may not work in all versions)

## Method 5: Reinstall/Reset Streamlit Config

If nothing else works:

1. **Stop Streamlit** (Ctrl+C in terminal)
2. **Delete Streamlit config** (if exists):
   ```bash
   rm -rf ~/.streamlit/config.toml
   ```
3. **Restart Streamlit**

---

## Quick Fix Script

You can also create a simple HTML file to clear the storage:

1. Create a file `fix_sidebar.html`:
   ```html
   <!DOCTYPE html>
   <html>
   <head>
       <title>Fix Streamlit Sidebar</title>
   </head>
   <body>
       <h1>Streamlit Sidebar Fix</h1>
       <button onclick="clearStorage()">Clear Sidebar State</button>
       <script>
       function clearStorage() {
           localStorage.removeItem('sidebarState');
           localStorage.removeItem('sidebarCollapsed');
           alert('Storage cleared! Now go back to your Streamlit app and refresh.');
       }
       </script>
   </body>
   </html>
   ```

2. Open it in your browser and click the button
3. Go back to Streamlit and refresh

---

**Note**: The sidebar state is stored in your browser's localStorage, so it persists across page refreshes. That's why restarting Streamlit doesn't help - you need to clear the browser storage or use the console method.

