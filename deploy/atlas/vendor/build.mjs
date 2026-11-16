import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
const here=path.dirname(fileURLToPath(import.meta.url)),root=path.resolve(here,'../../..'),out=path.join(root,'leakscope_app/static/vendor');
await build({stdin:{contents:"import * as m from 'maplibre-gl';m.setWorkerUrl('/static/vendor/maplibre/maplibre-gl-worker.mjs');window.maplibregl=m;",resolveDir:here},bundle:true,format:'iife',minify:true,outfile:path.join(out,'maplibre/maplibre-gl.js')});
await build({stdin:{contents:"import {Viewer} from '@photo-sphere-viewer/core';window.PhotoSphereViewer={Viewer};",resolveDir:here},bundle:true,format:'iife',minify:true,outfile:path.join(out,'panorama/viewer.js')});
for(const name of ['maplibre-gl.css','maplibre-gl-worker.mjs','maplibre-gl-shared.mjs'])fs.copyFileSync(path.join(here,'node_modules/maplibre-gl/dist',name),path.join(out,'maplibre',name));
fs.copyFileSync(path.join(here,'node_modules/@photo-sphere-viewer/core/index.css'),path.join(out,'panorama/viewer.css'));
