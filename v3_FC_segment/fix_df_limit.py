content = open('/opt/inv-allocation/app.py').read()

old = '''        st.caption(f"{len(show_p):,} records")
        st.dataframe(
            show_p[["day","date","item_number","seg","action",'''

new = '''        st.caption(f"{len(show_p):,} records")
        display_p = show_p.head(500)
        if len(show_p) > 500:
            st.info(f"Showing first 500 of {len(show_p):,} records. Use filters to narrow down.")
        st.dataframe(
            display_p[["day","date","item_number","seg","action",'''

if old in content:
    content2 = content.replace(old, new)
    open('/opt/inv-allocation/app.py', 'w').write(content2)
    print("Fixed: limited dataframe to 500 rows")
else:
    print("Pattern not found, trying alternative...")
    # Just limit the dataframe display with a simpler approach
    # Find and replace show_p[ in the dataframe call
    print(content[content.find('st.caption(f"{len(show_p)'):content.find('st.caption(f"{len(show_p)')+200])
