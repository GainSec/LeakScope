(function () {
  "use strict";
  const collator = new Intl.Collator(undefined, {numeric: true, sensitivity: "base"});
  const missing = value => value == null || value === "" || value === "—";
  function normalize(value, type) {
    if (missing(value) || (typeof value === "string" && !value.trim())) return null;
    if (type === "number") {
      const number = typeof value === "number" ? value : Number(String(value).replaceAll(",", "").trim());
      return Number.isFinite(number) ? number : null;
    }
    if (type === "date") {
      const stamp = typeof value === "number" ? value : Date.parse(value);
      return Number.isFinite(stamp) ? stamp : null;
    }
    return String(value).trim();
  }
  function compare(left, right, type, direction) {
    const a = normalize(left, type), b = normalize(right, type);
    if (a == null || b == null) return a == null ? (b == null ? 0 : 1) : -1;
    const value = type === "number" || type === "date" ? a - b : collator.compare(a, b);
    return direction === "desc" ? -value : value;
  }
  function sort(items, state, columns) {
    const values = Array.from(items || []), column = columns?.find(item => item?.key === state?.key);
    if (!column) return values;
    const value = column.value || (item => item?.[column.key]);
    return values.map((item, index) => ({item, index})).sort((a, b) =>
      compare(value(a.item), value(b.item), column.type, state.direction) || a.index - b.index,
    ).map(row => row.item);
  }
  function defaults(headers) {
    return Array.from(headers, header => {
      const label = typeof header === "string" ? header : header.textContent.trim();
      if (!label || /^(select|actions?|proof|evidence|evidence links|evidence \/ inspection|cited sources)$/i.test(label)) return null;
      return {key: label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, ""),
        type: /\b(?:seen|observed|collected|expires|started|checked|date|updated)\b/i.test(label) ? "date"
          : /^(ttl|confidence|observations|endpoints|watch matches|current count)$/i.test(label) ? "number" : "text"};
    });
  }
  function cellValue(cell) {
    if (!cell) return null;
    const source = cell.hasAttribute("data-sort-value") ? cell : cell.querySelector("[data-sort-value]");
    if (source) return source.getAttribute("data-sort-value");
    const timestamp = cell.querySelector("time[datetime]");
    if (timestamp) return timestamp.getAttribute("datetime");
    const copy = cell.cloneNode(true);
    copy.querySelectorAll("[hidden], [aria-hidden=true], details:not([open]) > :not(summary)").forEach(node => node.remove());
    return copy.textContent.trim();
  }
  function enhance(table, options = {}) {
    if (!table?.tHead || !table.tBodies[0] || table.dataset.sortEnhanced || table.dataset.sortable === "false") return table;
    table.dataset.sortEnhanced = "true";
    const headers = Array.from(table.tHead.rows[0]?.cells || []);
    const columns = options.columns || defaults(headers);
    const state = options.state || {key: null, direction: "asc"};
    const body = table.tBodies[0], original = Array.from(body.rows);
    const dataByRow=options.data ? new Map(original.map((row,index)=>[row,options.data[index]])) : null;
    const localValue=(column,index)=>row=>column.value ? column.value(dataByRow ? dataByRow.get(row) : row) : cellValue(row.cells[index]);
    if (options.scope) {
      table.dataset.sortScope = options.scope;
      const caption = table.caption || table.createCaption();
      if (caption.textContent) caption.append(document.createTextNode(" · "));
      const scope = document.createElement("span");scope.className = "ls-table-sort-scope";scope.textContent = options.scope;caption.append(scope);
    }
    const buttons = [];
    function updateHeaders() {
      buttons.forEach(({button, header, column, indicator}) => {
        const active = state.key === column.key;
        header.setAttribute("aria-sort", active ? (state.direction === "desc" ? "descending" : "ascending") : "none");
        indicator.textContent = active ? (state.direction === "desc" ? "↓" : "↑") : "↕";
        const next = active && state.direction === "asc" ? "descending" : "ascending";
        button.title = (column.description ? column.description + ". " : "") + "Sort " + next;
        button.setAttribute("aria-label", "Sort by " + button.querySelector(".ls-table-sort-label").textContent + ", " + (active ? "currently " + header.getAttribute("aria-sort") + ". " : "") + "Activate for " + next + ".");
      });
    }
    headers.forEach((header, index) => {
      const column = columns[index];header.scope = "col";
      if (!column?.key) return;
      const button = document.createElement("button");button.type = "button";button.className = "ls-table-sort";button.dataset.sortKey = column.key;
      const label = document.createElement("span");label.className = "ls-table-sort-label";label.textContent = header.textContent;
      const indicator = document.createElement("span");indicator.className = "ls-table-sort-indicator";indicator.setAttribute("aria-hidden", "true");button.append(label, indicator);
      button.addEventListener("click", () => {
        state.direction = state.key === column.key && state.direction === "asc" ? "desc" : "asc";
        state.key = column.key;if(options.onSort)state.focus = column.key;updateHeaders();
        if (options.onSort) options.onSort(state);
        else {
          const value = localValue(column,index);
          body.replaceChildren(...sort(original, state, [{...column, value}]));
        }
      });
      header.replaceChildren(button);buttons.push({button, header, column, indicator});
    });
    updateHeaders();
    if (!options.onSort && state.key) {
      const index=columns.findIndex(column=>column?.key===state.key);
      if(index>=0)body.replaceChildren(...sort(original,state,[{...columns[index],value:localValue(columns[index],index)}]));
    }
    if (state.focus) {
      const key = state.focus;delete state.focus;
      queueMicrotask(() => {
        const active=document.activeElement;
        if(table.isConnected && (!active || active===document.body || active===document.documentElement))
          buttons.find(item=>item.column.key===key)?.button.focus({preventScroll:true});
      });
    }
    return table;
  }
  function fromParams(params, keys, fallback, direction = "desc") {
    return {key: keys.includes(params.get("sort")) ? params.get("sort") : fallback,
      direction: ["asc", "desc"].includes(params.get("direction")) ? params.get("direction") : direction};
  }
  function columns(table) {
    const headers = Array.from(table?.tHead?.rows[0]?.cells || []), seen = new Map();
    return headers.map((header, index) => {
      const label = header.querySelector(".ls-table-sort-label")?.textContent?.trim() || header.textContent.trim() || "Column " + (index + 1);
      let key = header.querySelector("[data-sort-key]")?.dataset.sortKey || label.toLowerCase().replace(/[^a-z0-9]+/g,"_").replace(/^_|_$/g,"") || "column_"+(index+1);
      const duplicate = seen.get(key) || 0;seen.set(key,duplicate+1);if(duplicate)key += "_"+(duplicate+1);
      const protectedColumn = /^(select|selection|actions?|checkbox|evidence \/ inspection)$/i.test(label) || /\bactions?\b/i.test(label) || !!header.querySelector('input[type="checkbox"],input[type="radio"]') ||
        Array.from(table.tBodies).some(body=>Array.from(body.rows).some(row=>row.cells[index]?.querySelector('button,a.btn,[role="button"],input[type="checkbox"],input[type="radio"],.asset-actions')));
      return {key,label,index,protected:protectedColumn,visible:!header.hidden};
    });
  }
  function preferences(table) {
    const active=table?.querySelector('th[aria-sort="ascending"],th[aria-sort="descending"]');
    const sortKey=active?.querySelector('[data-sort-key]')?.dataset.sortKey;
    return {density:table?.dataset.workspaceDensity === "comfortable" ? "comfortable" : "compact",
      hidden_columns:columns(table).filter(column=>!column.protected && !column.visible).map(column=>column.key),
      ...(sortKey ? {sort:{key:sortKey,direction:active.getAttribute("aria-sort")==="descending"?"desc":"asc"}} : {})};
  }
  function applyPreferences(table, value = {}) {
    let visibilityChanged = false;
    const definitions=columns(table), hidden=new Set(Array.isArray(value.hidden_columns)?value.hidden_columns:[]);
    const data=definitions.filter(column=>!column.protected);
    if(data.length && data.every(column=>hidden.has(column.key)))hidden.delete(data[0].key);
    for(const column of definitions) {
      const conceal=!column.protected && hidden.has(column.key);
      for(const row of Array.from(table.rows))if(!row.dataset.empty && row.cells[column.index] && row.cells[column.index].hidden!==conceal){row.cells[column.index].hidden=conceal;visibilityChanged=true;}
    }
    const density=value.density === "comfortable" ? "comfortable" : "compact";
    if(table.dataset.workspaceDensity!==density)table.dataset.workspaceDensity=density;
    if(value.sort && ["asc","desc"].includes(value.sort.direction)) {
      const control=Array.from(table.querySelectorAll('thead [data-sort-key]')).find(button=>button.dataset.sortKey===value.sort.key);
      if(control)for(let attempt=0;attempt<2;attempt++) {
        if(control.closest('th')?.getAttribute('aria-sort')===(value.sort.direction==="desc"?"descending":"ascending"))break;
        control.click();
      }
    }
    if(visibilityChanged)table.dispatchEvent(new CustomEvent('leakscope:table-layout'));
    return table;
  }
  const disclosureState = new Map();
  function collapse(table, options = {}) {
    const existing = table?.closest?.('.ls-results-section');
    if (existing) return existing;
    if (!table?.tBodies[0] || !table.parentNode || table.dataset.collapsible === 'false' || table.closest('details,dialog,[role="dialog"],form') || table.tBodies[0].rows.length < (options.threshold ?? 20)) return null;
    const title = options.title || table.caption?.textContent.split(' · ')[0].trim() || 'Saved results';
    const key = 'table:' + (options.key || table.dataset.workspaceKey || table.id || title + ':' + Array.from(table.tHead?.rows[0]?.cells || [], cell => cell.querySelector('.ls-table-sort-label')?.textContent || cell.textContent).join('|'));
    const section = document.createElement('details');section.className = 'ls-results-section';section.dataset.workspaceDisclosure = key.slice(0,200);
    section.open = disclosureState.get(location.pathname + key) === true;
    const summary = document.createElement('summary'), label = document.createElement('strong'), count = document.createElement('span');
    label.textContent = title;count.className = 'ls-results-count';count.textContent = table.tBodies[0].rows.length.toLocaleString() + ' loaded rows';summary.append(label,count);section.append(summary);
    section.addEventListener('toggle', () => {if(disclosureState.size > 128)disclosureState.delete(disclosureState.keys().next().value);disclosureState.set(location.pathname + key,section.open)});
    const host = table.parentElement.matches('.table-scroll') ? table.parentElement : table;
    host.replaceWith(section);section.append(host);return section;
  }
  function browse(table, options = {}) {
    const make = (tag, className, text) => {const node=document.createElement(tag);if(className)node.className=className;if(text!=null)node.textContent=text;return node};
    const state=options.state || {};state.query ||= '';state.filters ||= {};state.sort ||= {key:null,direction:'asc'};state.pageSize ||= options.pageSize || 50;state.page ||= 1;
    const original=Array.from(table.tBodies[0].rows), headers=Array.from(table.tHead.rows[0].cells), definitions=options.columns || defaults(headers);
    const searchable=headers.map((header,index)=>({index,label:header.textContent.trim(),column:definitions[index]})).filter(item=>item.column);
    const cached=new Map(original.map(row=>[row,searchable.map(({index})=>String(cellValue(row.cells[index]) ?? '').toLocaleLowerCase())]));
    const shell=make('div','ls-table-browser'), toolbar=make('div','ls-table-tools'), footer=make('div','ls-table-footer'), viewport=make('div','table-scroll ls-table-viewport');
    const query=make('input','input');query.type='search';query.name='table_search';query.value=state.query;query.placeholder='Search loaded rows';
    const choices=(name,values,value)=>{const node=make('select','select');node.name=name;for(const [key,label]of values){const option=make('option','',label);option.value=key;node.append(option)}node.value=value;return node};
    const field=(label,control)=>{const node=make('label','field'),caption=make('span','field-label',label);node.append(caption,control);return node};
    const sortSelect=choices('table_sort',[['','Stored order'],...searchable.map(item=>[item.column.key,item.label])],state.sort.key || '');
    const direction=choices('table_direction',[['asc','Ascending'],['desc','Descending']],state.sort.direction);
    const size=choices('table_size',[25,50,100].map(n=>[String(n),String(n)+' rows']),String(state.pageSize));
    if(!size.value){state.pageSize=50;size.value='50'}
    const clear=make('button','btn btn-small','Clear filters');clear.type='button';clear.dataset.tableClear='';
    toolbar.append(field('Search this table',query));
    const filters=(options.filters || []).map(filter=>{const control=choices('table_'+filter.key,filter.options,state.filters[filter.key] || '');toolbar.append(field(filter.label,control));control.addEventListener('change',()=>{state.filters[filter.key]=control.value;state.page=1;render()});return {...filter,control}});
    toolbar.append(field('Sort by',sortSelect),field('Direction',direction),field('Rows per page',size),clear);
    const scope=make('p','ls-table-scope','Search, filters, sorting and paging apply to loaded rows only. Page and map scope filters remain unchanged.');
    const status=make('span','ls-table-status');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
    const previous=make('button','btn btn-small','Previous rows'),next=make('button','btn btn-small','Next rows');previous.type=next.type='button';footer.append(status,previous,next);
    function syncSort() {
      sortSelect.value=state.sort.key || '';direction.value=state.sort.direction;direction.disabled=!state.sort.key;
      for(const header of headers){const control=header.querySelector('[data-sort-key]');if(!control)continue;const active=control.dataset.sortKey===state.sort.key;header.setAttribute('aria-sort',active?(state.sort.direction==='desc'?'descending':'ascending'):'none');const indicator=control.querySelector('.ls-table-sort-indicator');if(indicator)indicator.textContent=active?(state.sort.direction==='desc'?'↓':'↑'):'↕';const next=active&&state.sort.direction==='asc'?'descending':'ascending';control.title='Sort '+next;control.setAttribute('aria-label','Sort by '+control.querySelector('.ls-table-sort-label').textContent+', '+(active?'currently '+header.getAttribute('aria-sort')+'. ':'')+'Activate for '+next+'.')}
    }
    // Export takes every row matching this table's search and filters, in the shown order, not only the page.
    let matching=original;table.lsExportRows=()=>matching;
    function render() {
      const text=state.query.trim().toLocaleLowerCase();
      let rows=original.filter(row=>(!text || cached.get(row).some((value,i)=>!headers[searchable[i].index].hidden && value.includes(text))) && filters.every(filter=>!state.filters[filter.key] || filter.match(row,state.filters[filter.key])));
      const matched=rows.length;
      const index=definitions.findIndex(column=>column?.key===state.sort.key);
      if(index>=0)rows=sort(rows,state.sort,[{...definitions[index],value:row=>cellValue(row.cells[index])}]);
      const pages=Math.max(1,Math.ceil(matched/state.pageSize));state.page=Math.max(1,Math.min(pages,state.page));const start=(state.page-1)*state.pageSize;
      matching=rows;
      const selected=rows.slice(start,start+state.pageSize);for(const row of selected)headers.forEach((header,index)=>{if(row.cells[index])row.cells[index].hidden=header.hidden});
      table.tBodies[0].replaceChildren(...selected);
      if(!matched){const row=make('tr');row.dataset.empty='true';const cell=make('td','ls-table-empty',original.length?'No loaded rows match these table filters.':'No saved rows in this scope.');cell.colSpan=headers.length;row.append(cell);table.tBodies[0].append(row)}
      status.textContent=matched.toLocaleString()+' matching / '+original.length.toLocaleString()+' loaded rows · '+(matched?start+1:0)+'–'+Math.min(start+state.pageSize,matched)+' · Page '+state.page+' of '+pages;
      previous.disabled=state.page<=1;next.disabled=state.page>=pages;clear.disabled=!state.query && !filters.some(filter=>state.filters[filter.key]);syncSort();options.onCounts?.({total:original.length,matched,page:state.page,pageSize:state.pageSize});
    }
    enhance(table,{columns:definitions,state:state.sort,onSort:()=>{state.page=1;render()}});
    const chooseSort=()=>{const key=sortSelect.value;if(!key){state.sort.key=null;state.page=1;render();return}const desired=direction.value,control=Array.from(table.querySelectorAll('thead [data-sort-key]')).find(button=>button.dataset.sortKey===key);if(!control)return;if(state.sort.key!==key || state.sort.direction!==desired){control.click();if(state.sort.direction!==desired)control.click()}};
    sortSelect.addEventListener('change',chooseSort);direction.addEventListener('change',chooseSort);
    query.addEventListener('input',()=>{state.query=query.value;state.page=1;render()});size.addEventListener('change',()=>{state.pageSize=Number(size.value);state.page=1;render()});
    clear.addEventListener('click',()=>{state.query='';query.value='';filters.forEach(filter=>{state.filters[filter.key]='';filter.control.value=''});state.page=1;render();query.focus()});
    previous.addEventListener('click',()=>{state.page--;render()});next.addEventListener('click',()=>{state.page++;render()});
    table.addEventListener('leakscope:table-layout',render);
    viewport.append(table);shell.append(toolbar,scope,viewport,footer);render();return shell;
  }
  // No table scrolls sideways: cells wrap, and when the space is too narrow for the number of
  // columns each row stacks with its column names. Matrices (data-sortable="false") opt out.
  const STACK_PER_COLUMN = 125, fitted = new WeakSet();
  function shouldStack(width, columnCount) { return width > 0 && width < columnCount * STACK_PER_COLUMN; }
  function fit(table) {
    const head = table?.tHead?.rows[0];
    if (!head || !table.tBodies[0] || table.dataset.fit === 'false' || table.dataset.sortable === 'false') return false;
    table.classList.add('ls-fit');
    const names = Array.from(head.cells, cell => (cell.querySelector('.ls-table-sort-label')?.textContent || cell.textContent).trim());
    for (const body of table.tBodies) for (const row of body.rows) Array.from(row.cells).forEach((cell, index) => { if (cell.colSpan === 1 && cell.dataset.label !== names[index]) cell.dataset.label = names[index] || ''; });
    if (!fitted.has(table) && typeof ResizeObserver === 'function' && table.parentElement) {
      fitted.add(table);
      new ResizeObserver(entries => { for (const entry of entries) settle(table, names.length, entry.contentRect.width); }).observe(table.parentElement);
    }
    settle(table, names.length);
    return true;
  }
  // The column rule decides first; then the table is measured. Content that still does not fit is allowed to
  // break anywhere (.is-tight), and if even that is too wide the rows stack.
  function settle(table, columnCount, width) {
    const box = table.parentElement;
    if (!box) return;
    const space = width == null ? box.clientWidth : width;
    if (!space) return;
    table.classList.remove('is-tight');
    table.classList.toggle('is-stacked', shouldStack(space, columnCount));
    if (!box.clientWidth || box.scrollWidth <= box.clientWidth + 1) return;
    table.classList.add('is-tight');
    if (box.scrollWidth > box.clientWidth + 1) table.classList.add('is-stacked');
  }
  window.LeakScopeTables = {enhance, sort, defaults, fromParams, columns, preferences, applyPreferences, collapse, browse, fit, shouldStack};
})();
