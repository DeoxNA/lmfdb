# -*- coding: utf-8 -*-
# This Blueprint is about Higher Genus Curves
# Author: Jen Paulhus (copied from John Jones Local Fields)

import re
import pymongo
ASC = pymongo.ASCENDING
import flask
from lmfdb import base
from lmfdb.base import app, getDBConnection
from flask import render_template, render_template_string, request, abort, Blueprint, url_for, make_response
from lmfdb.utils import ajax_more, image_src, web_latex, to_dict, coeff_to_poly, pol_to_html, make_logger
from lmfdb.search_parsing import parse_ints, parse_count, parse_start, parse_list, clean_input

from sage.all import Permutation

from lmfdb.higher_genus_w_automorphisms import higher_genus_w_automorphisms_page, logger

from lmfdb.genus2_curves.data import group_dict


def get_bread(breads=[]):
    bc = [("Higher Genus/C/aut", url_for(".index"))]
    for b in breads:
        bc.append(b)
    return bc



def groupid_to_meaningful(groupid):
    if groupid[0] < 120:
        return group_dict[str(groupid).replace(" ", "")]
    elif groupid[0]==168 and groupid[1]==42:
        return 'PSL(2,7)'
    elif groupid[0]==120 and groupid[1]==34:
        return 'S_5'
    else:    
        return str(groupid)

def tfTOyn(bool):
    if bool:
        return "Yes"
    else:
        return "No"
    
def group_display_shortC(C):
    def gds(nt):
        return group_display_short(nt[0], nt[1], C)
    return gds

    
    
@higher_genus_w_automorphisms_page.route("/")
def index():
    bread = get_bread()
    if request.args:
        return higher_genus_w_automorphisms_search(**request.args)
    genus_list = range(2,6)
    info = {'count': 20,'genus_list': genus_list}
    

    learnmore = [('Completeness of the data', url_for(".completeness_page")),
                ('Source of the data', url_for(".how_computed_page")),
                ('Labeling convention', url_for(".labels_page"))]
    
    return render_template("hgcwa-index.html", title="Higher Genus Curves with Automorphisms", bread=bread,  info=info, learnmore=learnmore)




@higher_genus_w_automorphisms_page.route("/<label>")
def by_label(label):
    return render_hgcwa_webpage({'label': label})



@higher_genus_w_automorphisms_page.route("/search", methods=["GET", "POST"])
def search():
    if request.method == "GET":
        val = request.args.get("val", "no value")
        bread = get_bread([("Search for '%s'" % val, url_for('.search'))])
        return render_template("hgcwa-search.html", title="Automorphisms of Higher Genus Curves Search", bread=bread, val=val)
    elif request.method == "POST":
        return "ERROR: we always do http get to explicitly display the search parameters"
    else:
        return flask.redirect(404)


def higher_genus_w_automorphisms_search(**args):
    info = to_dict(args)
    bread = get_bread([("Search results", url_for('.search'))])
    C = base.getDBConnection()
    query = {}
    if 'jump_to' in info:
        return render_hgcwa_webpage({'label': info['jump_to']})

    try:
        parse_list(info,query,'group', name='Group')
        parse_ints(info,query,'genus',name='Genus')
        parse_list(info,query,'signature',name='Signature')
    except ValueError:
        return search_input_error(info, bread)
    count = parse_count(info)
    start = parse_start(info)
    
    res = C.curve_automorphisms.families.find(query).sort([(
         'g', pymongo.ASCENDING), ('dim', pymongo.ASCENDING)])
    nres = res.count()
    res = res.skip(start).limit(count)

    if(start >= nres):
        start -= (1 + (start - nres) / count) * count
    if(start < 0):
        start = 0

    info['fields'] = res
    info['number'] = nres
    info['group_display'] = group_display_shortC(C)
    info['sign_display'] = sign_display
    info['start'] = start
    if nres == 1:
        info['report'] = 'unique match'
    else:
        if nres > count or start != 0:
            info['report'] = 'displaying matches %s-%s of %s' % (start + 1, min(
                               nres, start + count), nres)
        else:
            info['report'] = 'displaying all %s matches' % nres

    return render_template("hgcwa-search.html", info=info, title="Higher Genus Curves with Automorphisms Search Result", bread=bread)





def render_hgcwa_webpage(args):
    data = None
    info = {}
    if 'label' in args:
        label = clean_input(args['label'])
        C = base.getDBConnection()
        data = C.curve_automorphisms.families.find({'label': label})
        numentries = data.count()
        spdata = False
        
        if data is None:
            bread = get_bread([("Search error", url_for('.search'))])
            info['err'] = "Higher Genus Curve with Automorphism " + label + " was not found in the database."
            info['label'] = label
            return search_input_error(info, bread)
        if numentries == 1:
            dataz=data[0]
            g = dataz['genus']
            GG = dataz['group']
            gn = GG[0]
            gt = GG[1]
    

            group = groupid_to_meaningful(dataz['group'])
            if group == str(GG) or group == "[" + str(gn)+","+str(gt)+"]":
                spname=False
            else:
                spname=True
            title = 'Family of genus ' + str(g) + ' curves with automorphism group $' + group +'$'
            smallgroup="(" + str(gn) + "," +str(gt) +")"   

            prop2 = [
                ('Genus', '\(%d\)' % g),
                ('Small Group', '\(%s\)' %  smallgroup),
                ('Signature', '\(%s\)' % sign_display(dataz['signature'])),
                ('Number of Topologically <br>  Inequivalent Actions', '\(%d\)' % numentries)
            ]
            info.update({'genus': dataz['genus'],
                    'genvecs': dataz['gen_vectors'],
                    'sign': sign_display(dataz['signature']),   
                    'group': groupid_to_meaningful(dataz['group']),
                    'g0':dataz['g0'],
                    'dim':dataz['dim'],
                    'r':dataz['r'],
                    'gpid': smallgroup
                   })

            if spname:
                info.update({'specialname': True})
        		   
            if 'eqn' in dataz:
                info.update({'eqn': dataz['eqn']})

            if 'hyperelliptic' in dataz:
                info.update({'ishyp':  tfTOyn(dataz['hyperelliptic'])})
                spdata = True
                
            if 'hyp_involution' in dataz:
                info.update({'hypinv': dataz['hyp_involution']})
            
            gg = "/GaloisGroup/" + str(gn) + "T" + str(gt)
            
            if 'full_auto' in dataz:
                info.update({'fullauto': groupid_to_meaningful(dataz['full_auto']),
                         'signH':sign_display(dataz['signH']),
                         'higgenlabel' : dataz['full_label'] })
                higgenstrg = "/HigherGenus/C/aut/" + dataz['full_label']
                friends = [('Family of full automorphisms',  higgenstrg  )]
            else:
                friends = [ ]
        
            if spdata:
                info.update({'spdata': spdata})
        
            bread = get_bread([(label, ' ')])
            learnmore =[('Completeness of the data', url_for(".completeness_page")),
                ('Source of the data', url_for(".how_computed_page")),
                ('Labeling convention', url_for(".labels_page"))]

            downloads = [('Download this example', '.')]
            
            return render_template("hgcwa-show-curve.html", 
                               title=title, bread=bread, info=info,
                               properties2=prop2, friends=friends,
                               learnmore=learnmore, downloads=downloads)
        else:
            Ldata= [ ]
            for d in data:
                Ldata.append(d) 
            
            dataz=Ldata[0]
            g = dataz['genus']
            GG = dataz['group']
            gn = GG[0]
            gt = GG[1]
            info['fields'] = Ldata
            info['tfTOyn'] = tfTOyn
            info['sign_display']=sign_display
            info['groupid_to_meaningful']= groupid_to_meaningful

            group = groupid_to_meaningful(dataz['group'])
            if group == str(GG) or group == "[" + str(gn)+","+str(gt)+"]":
                spname=False
            else:
                spname=True
            if spname:
                info.update({'specialname': True})

            title = 'Family of genus ' + str(g) + ' curves with automorphism group $' + group +'$'
            smallgroup="(" + str(gn) + "," +str(gt) +")"


            info.update({'g': g,
                    'sign': sign_display(dataz['signature']),   
                    'group': group,
                    'g0':dataz['g0'],
                    'dim':dataz['dim'],
                    'r':dataz['r'],
                    'gpid': smallgroup,
                    'numb': numentries
                   })
            
            prop2 = [
                ('Genus', '\(%d\)' % g),
                ('Small Group', '\(%s\)' %  smallgroup),
                ('Signature', '\(%s\)' % sign_display(dataz['signature'])),
                ('Number of Topologically <br> Inequivalent Actions', '\(%d\)' % numentries)
            ]

            friends = [ ]
            for inddata in Ldata:
                if 'full_auto' in inddata:
                    higgenstrg = "/HigherGenus/C/aut/" + inddata['full_label']
                    friends.append(('Family of full automorphisms',  higgenstrg))

            learnmore =[('Completeness of the data', url_for(".completeness_page")),
                ('Source of the data', url_for(".how_computed_page")),
                ('Labeling convention', url_for(".labels_page"))]

            downloads = [('Download this example', '.')]
           
            bread = get_bread([(label, ' ')])
            return render_template("hgcwa-topequiv.html", info=info, title=title, bread=bread, learnmore=learnmore, properties2=prop2, friends=friends,  downloads=downloads)
            

def perm_display(L):
    return [Permutation(ell).cycle_string()  for ell in L]

        
def sign_display(L):
    sizeL = len(L)                
    signL = "[ " + str(L[0]) + "; "
    for i in range(1,sizeL-1):
        signL= signL + str(L[i]) + ", "                    
     
    signL=signL + str(L[sizeL-1]) + " ]"                  
    return signL                



def search_input_error(info, bread):
    return render_template("hgcwa-search.html", info=info, title='Higher Genus Curve Search Input Error', bread=bread)


 
@higher_genus_w_automorphisms_page.route("/Completeness")
def completeness_page():
    t = 'Completeness of the automorphisms of curves data'
    bread = get_bread([("Completeness", )])
    learnmore = [('Source of the data', url_for(".how_computed_page")),
                ('Labeling convention', url_for(".labels_page"))]
    return render_template("single.html", kid='dq.curve.highergenus.aut.extent',
                            title=t, bread=bread,
                           learnmore=learnmore)


@higher_genus_w_automorphisms_page.route("/Labels")
def labels_page():
    t = 'Label scheme for the data'
    bread = get_bread([("Labels", '')])
    learnmore = [('Completeness of the data', url_for(".completeness_page")),
                ('Source of the data', url_for(".how_computed_page"))]
    return render_template("single.html", kid='dq.curve.highergenus.aut.label',
                           learnmore=learnmore,  title=t,
                           bread=bread)

@higher_genus_w_automorphisms_page.route("/Source")
def how_computed_page():
    t = 'Source of the automorphisms of curve data'
    bread = get_bread([("Source", '')])
    learnmore = [('Completeness of the data', url_for(".completeness_page")),
                ('Labeling convention', url_for(".labels_page"))]
    return render_template("single.html", kid='dq.curve.highergenus.aut.source',
                            title=t, bread=bread, 
                           learnmore=learnmore)
