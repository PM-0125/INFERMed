# Sidebar Troubleshooting Guide

## Problem: Sidebar is Hidden and `stSidebarCollapseButton` returns `undefined`

This means Streamlit is using a different selector or the sidebar is completely hidden.

## Solution 1: Recreate Config File ✅

The `.streamlit/config.toml` file has been recreated. **Restart Streamlit** for it to take effect:

```bash
# Stop Streamlit (Ctrl+C)
# Then restart:
streamlit run src/frontend/app.py
```

## Solution 2: Find the Correct Selector

Since `stSidebarCollapseButton` returns `undefined`, try these in the browser console (F12):

### Method 1: Find all buttons and look for sidebar toggle
```javascript
// List all buttons to find the sidebar toggle
Array.from(document.querySelectorAll('button')).forEach((btn, i) => {
    const label = btn.getAttribute('aria-label') || btn.textContent || '';
    if (label.toLowerCase().includes('sidebar') || label.toLowerCase().includes('menu')) {
        console.log(`Button ${i}:`, label, btn);
    }
});
```

### Method 2: Try common Streamlit selectors
```javascript
// Try different possible selectors
const selectors = [
    'button[kind="header"]',
    '[data-testid="stSidebarCollapseButton"]',
    '[aria-label*="sidebar"]',
    '[aria-label*="menu"]',
    'button[aria-label*="Close"]',
    'button[aria-label*="Open"]',
    '.css-1d391kg button',
    'header button',
    '[class*="sidebar"] button'
];

selectors.forEach(sel => {
    const el = document.querySelector(sel);
    if (el) {
        console.log(`Found with selector "${sel}":`, el);
        el.click(); // Try clicking it
    }
});
```

### Method 3: Direct DOM manipulation
```javascript
// Force show the sidebar directly
const sidebar = document.querySelector('[data-testid="stSidebar"]');
if (sidebar) {
    sidebar.style.display = 'block';
    sidebar.style.visibility = 'visible';
    sidebar.style.width = '21rem'; // Default Streamlit sidebar width
    sidebar.setAttribute('aria-expanded', 'true');
    console.log('Sidebar forced to visible');
} else {
    console.log('Sidebar element not found');
}
```

### Method 4: Clear ALL storage
```javascript
// Nuclear option - clear everything
localStorage.clear();
sessionStorage.clear();
location.reload();
```

## Solution 3: Check Streamlit Version

Different Streamlit versions use different selectors. Check your version:

```bash
streamlit --version
```

Then look up the correct selector for that version.

## Solution 4: Use Streamlit's Built-in Menu

If the sidebar toggle button doesn't exist, Streamlit might have moved it to the menu:

1. Look for a **hamburger menu** (☰) in the top-right corner
2. Click it and look for "Settings" or "Sidebar" option

## Solution 5: Manual CSS Override

If nothing else works, inject CSS to force the sidebar visible:

```javascript
// Inject CSS to force sidebar visible
const style = document.createElement('style');
style.textContent = `
    [data-testid="stSidebar"] {
        display: block !important;
        visibility: visible !important;
        width: 21rem !important;
    }
`;
document.head.appendChild(style);
```

## Why This Happens

1. **Browser localStorage** stores the sidebar state
2. **Streamlit version differences** use different selectors
3. **Config file missing** means Streamlit uses defaults
4. **Sidebar might be completely removed** in some Streamlit versions

## Prevention

The `.streamlit/config.toml` file has been recreated with:
- Proper theme settings
- Sidebar configuration
- Server settings

**Always restart Streamlit after creating/modifying the config file!**

