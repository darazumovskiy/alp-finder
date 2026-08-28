import json, numpy as np, rasterio, os, sys
from rasterio.windows import from_bounds
from rasterio.warp import transform_bounds
os.environ['AWS_NO_SIGN_REQUEST']='YES'; os.environ['GDAL_HTTP_MULTIPLEX']='YES'; os.environ['GDAL_DISABLE_READDIR_ON_OPEN']='EMPTY_DIR'
lon0,lat0=73.592,39.478; d=0.004
bbox=[lon0-d,lat0-d*0.77,lon0+d,lat0+d*0.77]
items={i['id']:i for i in json.load(open('s2_items.json'))}
def rd(href):
    with rasterio.open(href) as ds:
        b=transform_bounds('EPSG:4326',ds.crs,*bbox); w=from_bounds(*b,ds.transform)
        return ds.read(1,window=w).astype(np.float32)
print('date      cloudfree%  rock%(NDSI<0.4)  meanB11(snow)  meanB03(snow)', flush=True)
for wid in sorted(items):
    it=items[wid]; A=it['assets']
    if it['properties'].get('eo:cloud_cover',100)>70: continue
    try:
        g=rd(A['green']['href']); sw=rd(A['swir16']['href']); scl=rd(A['scl']['href'])
    except Exception as e: print(wid,'ERR',e, flush=True); continue
    sw=np.kron(sw,np.ones((2,2)))[:g.shape[0],:g.shape[1]]; scl=np.kron(scl,np.ones((2,2)))[:g.shape[0],:g.shape[1]]
    ndsi=(g-sw)/(g+sw+1e-6)
    ok=~np.isin(scl,[0,1,3,8,9,10])
    cf=ok.mean()*100
    snow=ok&(ndsi>0.4)
    rock=(ndsi[ok]<0.4).mean()*100 if ok.sum()>50 else float('nan')
    print(wid[9:17], '%6.0f     %6.1f          %6.0f      %6.0f'%(cf, rock, sw[snow].mean() if snow.sum() else -1, g[snow].mean() if snow.sum() else -1), flush=True)
