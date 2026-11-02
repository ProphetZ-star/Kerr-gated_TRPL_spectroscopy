// One palette for the TRPL map intensity and the Analysis delay overlays.
export const MAP_COLORS=[[43,48,91],[44,105,137],[76,195,217],[202,235,240]];
export const MAP_GRADIENT='linear-gradient(90deg,'+MAP_COLORS.map(c=>'rgb('+c.join(',')+')').join(',')+')';
export function mapColor(fraction){
 return 'rgb('+mapRGB(fraction).join(',')+')';
}
export function mapRGB(fraction){
 const f=Math.max(0,Math.min(1,fraction))*3,a=Math.min(2,Math.floor(f)),u=f-a;
 return MAP_COLORS[a].map((c,i)=>Math.round(c*(1-u)+MAP_COLORS[a+1][i]*u));
}
