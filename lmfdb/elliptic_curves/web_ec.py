# -*- coding: utf-8 -*-
import re
import os
import yaml
from flask import url_for
from lmfdb.base import getDBConnection
from lmfdb.utils import make_logger, web_latex, encode_plot, coeff_to_poly, web_latex_split_on_pm
from lmfdb.search_parsing import split_list
#from lmfdb.ecnf.WebEllipticCurve import db_ecnf
from lmfdb.modular_forms.elliptic_modular_forms.backend.emf_utils import newform_label, is_newform_in_db
from lmfdb.sato_tate_groups.main import st_link_by_name
from lmfdb.number_fields.number_field import field_pretty
from lmfdb.WebNumberField import nf_display_knowl, string2list

from sage.all import EllipticCurve, latex, ZZ, QQ, prod, Factorization, PowerSeriesRing, prime_range

ROUSE_URL_PREFIX = "http://users.wfu.edu/rouseja/2adic/" # Needs to be changed whenever J. Rouse and D. Zureick-Brown move their data

cremona_label_regex = re.compile(r'(\d+)([a-z]+)(\d*)')
lmfdb_label_regex = re.compile(r'(\d+)\.([a-z]+)(\d*)')
lmfdb_iso_label_regex = re.compile(r'([a-z]+)(\d*)')
sw_label_regex = re.compile(r'sw(\d+)(\.)(\d+)(\.*)(\d*)')
weierstrass_eqn_regex = re.compile(r'\[(-?\d+),(-?\d+),(-?\d+),(-?\d+),(-?\d+)\]')
short_weierstrass_eqn_regex = re.compile(r'\[(-?\d+),(-?\d+)\]')

def match_lmfdb_label(lab):
    return lmfdb_label_regex.match(lab)

def match_lmfdb_iso_label(lab):
    return lmfdb_iso_label_regex.match(lab)

def match_cremona_label(lab):
    return cremona_label_regex.match(lab)

def split_lmfdb_label(lab):
    return lmfdb_label_regex.match(lab).groups()

def split_lmfdb_iso_label(lab):
    return lmfdb_iso_label_regex.match(lab).groups()

def split_cremona_label(lab):
    return cremona_label_regex.match(lab).groups()

def curve_lmfdb_label(conductor, iso_class, number):
    return "%s.%s%s" % (conductor, iso_class, number)

def curve_cremona_label(conductor, iso_class, number):
    return "%s%s%s" % (conductor, iso_class, number)

def class_lmfdb_label(conductor, iso_class):
    return "%s.%s" % (conductor, iso_class)

def class_cremona_label(conductor, iso_class):
    return "%s%s" % (conductor, iso_class)

logger = make_logger("ec")

def db_ec():
    return getDBConnection().elliptic_curves.curves

def db_ecstats():
    return getDBConnection().elliptic_curves.curves.stats

def padic_db():
    return getDBConnection().elliptic_curves.padic_db

def is_ec_isogeny_class_in_db(label_isogeny_class):
    return db_ec().find({"lmfdb_iso" : label_isogeny_class} ).limit(1).count(True) > 0

def split_galois_image_code(s):
    """Each code starts with a prime (1-3 digits but we allow for more)
    followed by an image code or that prime.  This function returns
    two substrings, the prefix number and the rest.
    """
    p = re.findall(r'\d+', s)[0]
    return p, s[len(p):]

def trim_galois_image_code(s):
    """Return the image code with the prime prefix removed.
    """
    return split_galois_image_code(s)[1]

def parse_point(s):
    r""" Converts a string representing a point in affine or
    projective coordinates to a tuple of rationals.

    Sample input: '(-565,282)', '(4599/4,-4603/8)', '(555:10778:1)',
    '(1055:-32778:1)'
    """
    # strip parentheses and spaces
    s = s.replace(' ','')[1:-1]
    if ',' in s: # affine: comma-separated rationals
        return [QQ(str(c)) for c in s.split(',')]
    if ':' in s: # projective: colon-separated integers
        cc = [ZZ(str(c)) for c in s.split(':')]
        return [cc[0]/cc[2], cc[1]/cc[2]]
    return []

def parse_points(s):
    r""" Converts a list of strings representing points in affine or
    projective coordinates to a list of tuples of rationals.

    Sample input:  ['(-565,282)', '(4599/4,-4603/8)']
                   ['(555:10778:1)', '(1055:-32778:1)']
    """
    return [parse_point(P) for P in s]

class WebEC(object):
    """
    Class for an elliptic curve over Q
    """
    def __init__(self, dbdata):
        """
        Arguments:

            - dbdata: the data from the database
        """
        logger.debug("Constructing an instance of WebEC")
        self.__dict__.update(dbdata)
        # Next lines because the hyphens make trouble
        self.xintcoords = split_list(dbdata['x-coordinates_of_integral_points'])
        self.non_surjective_primes = dbdata['non-surjective_primes']
        self.non_maximal_primes = dbdata['non-maximal_primes']
        self.mod_p_images = dbdata['mod-p_images']

        # Next lines because the python identifiers cannot start with 2
        self.twoadic_index = dbdata['2adic_index']
        self.twoadic_log_level = dbdata['2adic_log_level']
        self.twoadic_gens = dbdata['2adic_gens']
        self.twoadic_label = dbdata['2adic_label']
        # All other fields are handled here
        self.make_curve()

    @staticmethod
    def by_label(label):
        """
        Searches for a specific elliptic curve in the curves
        collection by its label, which can be either in LMFDB or
        Cremona format.
        """
        try:
            N, iso, number = split_lmfdb_label(label)
            data = db_ec().find_one({"lmfdb_label" : label})
        except AttributeError:
            try:
                N, iso, number = split_cremona_label(label)
                data = db_ec().find_one({"label" : label})
            except AttributeError:
                return "Invalid label" # caller must catch this and raise an error

        if data:
            return WebEC(data)
        return "Curve not found" # caller must catch this and raise an error

    def make_curve(self):
        # To start with the data fields of self are just those from
        # the database.  We need to reformat these.

        # Old version: required constructing the actual elliptic curve
        # E, and computing some further data about it.

        # New version (May 2016): extra data fields now in the
        # database so we do not have to construct the curve or do any
        # computation with it on the fly.  As a failsafe the old way
        # is still included.

        data = self.data = {}
        try:
            data['ainvs'] = [int(c) for c in self.xainvs[1:-1].split(',')]
        except AttributeError:
            data['ainvs'] = [int(ai) for ai in self.ainvs]
        data['conductor'] = N = ZZ(self.conductor)
        data['j_invariant'] = QQ(str(self.jinv))
        data['j_inv_factor'] = latex(0)
        if data['j_invariant']: # don't factor 0
            data['j_inv_factor'] = latex(data['j_invariant'].factor())
        data['j_inv_str'] = unicode(str(data['j_invariant']))
        data['j_inv_latex'] = web_latex(data['j_invariant'])
        mw = self.mw = {}
        mw['rank'] = self.rank
        mw['int_points'] = ''
        if self.xintcoords:
            a1, a2, a3, a4, a6 = [ZZ(a) for a in data['ainvs']]
            def lift_x(x):
                f = ((x + a2) * x + a4) * x + a6
                b = (a1*x + a3)
                d = (b*b + 4*f).sqrt()
                return (x, (-b+d)/2)
            mw['int_points'] = ', '.join(web_latex(lift_x(x)) for x in self.xintcoords)

        mw['generators'] = ''
        mw['heights'] = []
        if self.gens:
            mw['generators'] = [web_latex(tuple(P)) for P in parse_points(self.gens)]

        mw['tor_order'] = self.torsion
        tor_struct = [int(c) for c in self.torsion_structure]
        if mw['tor_order'] == 1:
            mw['tor_struct'] = '\mathrm{Trivial}'
            mw['tor_gens'] = ''
        else:
            mw['tor_struct'] = ' \\times '.join(['\Z/{%s}\Z' % n for n in tor_struct])
            mw['tor_gens'] = ', '.join(web_latex(tuple(P)) for P in parse_points(self.torsion_generators))

        # try to get all the data we need from the database entry (now in self)
        try:
            data['equation'] = self.equation
            local_data = self.local_data
            D = self.signD * prod([ld['p']**ld['ord_disc'] for ld in local_data])
            data['disc'] = D
            Nfac = Factorization([(ZZ(ld['p']),ld['ord_cond']) for ld in local_data])
            Dfac = Factorization([(ZZ(ld['p']),ld['ord_disc']) for ld in local_data], unit=ZZ(self.signD))

            data['minq_D'] = minqD = self.min_quad_twist['disc']
            minq_label = self.min_quad_twist['label']
            data['minq_label'] = db_ec().find_one({'label':minq_label}, ['lmfdb_label'])['lmfdb_label']
            data['minq_info'] = '(itself)' if minqD==1 else '(by %s)' % minqD
            try:
                data['degree'] = self.degree
            except AttributeError:
                data['degree']  =0 # invalid, but will be displayed nicely
            mw['heights'] = self.heights
            if self.number == 1:
                data['an'] = self.anlist
                data['ap'] = self.aplist
            else:
                r = db_ec().find_one({'lmfdb_iso':self.lmfdb_iso, 'number':1}, ['anlist','aplist'])
                data['an'] = r['anlist']
                data['ap'] = r['aplist']

        # otherwise fall back to computing it from the curve
        except AttributeError:
            self.E = EllipticCurve(data['ainvs'])
            data['equation'] = web_latex(self.E)
            data['disc'] = D = self.E.discriminant()
            Nfac = N.factor()
            Dfac = D.factor()
            bad_primes = [p for p,e in Nfac]
            try:
                data['degree'] = self.degree
            except AttributeError:
                try:
                    data['degree'] = self.E.modular_degree()
                except RuntimeError:
                    data['degree'] = 0  # invalid, but will be displayed nicely
            minq, minqD = self.E.minimal_quadratic_twist()
            data['minq_D'] = minqD
            if minqD == 1:
                data['minq_label'] = self.lmfdb_label
                data['minq_info'] = '(itself)'
            else:
                # This relies on the minimal twist being in the
                # database, which is true when the database only
                # contains the Cremona database.  It would be a good
                # idea if, when the database is extended, we ensured
                # that for any curve included, all twists of smaller
                # conductor are also included.
                minq_ainvs = [str(c) for c in minq.ainvs()]
                data['minq_label'] = db_ec().find_one({'jinv':str(self.E.j_invariant()),
                                                       'ainvs': minq_ainvs},['lmfdb_label'])['lmfdb_label']
                data['minq_info'] = '(by %s)' % minqD

            if self.gens:
                self.generators = [self.E(g) for g in parse_points(self.gens)]
                mw['heights'] = [P.height() for P in self.generators]

            data['an'] = self.E.anlist(20,python_ints=True)
            data['ap'] = self.E.aplist(100,python_ints=True)
            self.local_data = local_data = []
            for p in bad_primes:
                ld = self.E.local_data(p, algorithm="generic")
                local_data_p = {}
                local_data_p['p'] = p
                local_data_p['cp'] = ld.tamagawa_number()
                local_data_p['kod'] = web_latex(ld.kodaira_symbol()).replace('$', '')
                local_data_p['red'] = ld.bad_reduction_type()
                rootno = -ld.bad_reduction_type()
                if rootno==0:
                    rootno = self.E.root_number(p)
                local_data_p['rootno'] = rootno
                local_data_p['ord_cond'] = ld.conductor_valuation()
                local_data_p['ord_disc'] = ld.discriminant_valuation()
                local_data_p['ord_den_j'] = max(0,-self.E.j_invariant().valuation(p))
                local_data.append(local_data_p)

        # If we got the data from the database, the root numbers may
        # not have been stored there, so we have to compute them.  If
        # there are additive primes this means constructing the curve.
        for ld in self.local_data:
            if not 'rootno' in ld:
                rootno = -ld['red']
                if rootno==0:
                    try:
                        E = self.E
                    except AttributeError:
                        self.E = E = EllipticCurve(data['ainvs'])
                    rootno = E.root_number(ld['p'])
                ld['rootno'] = rootno

        minq_N, minq_iso, minq_number = split_lmfdb_label(data['minq_label'])

        data['disc_factor'] = latex(Dfac)
        data['cond_factor'] =latex(Nfac)
        data['disc_latex'] = web_latex(D)
        data['cond_latex'] = web_latex(N)

        data['galois_images'] = [trim_galois_image_code(s) for s in self.mod_p_images]
        data['non_maximal_primes'] = self.non_maximal_primes
        data['galois_data'] = [{'p': p,'image': im }
                               for p,im in zip(data['non_maximal_primes'],
                                               data['galois_images'])]

        data['CMD'] = self.cm
        data['CM'] = "no"
        data['EndE'] = "\(\Z\)"
        if self.cm:
            data['cm_ramp'] = [p for p in ZZ(self.cm).support() if not p in self.non_surjective_primes]
            data['cm_nramp'] = len(data['cm_ramp'])
            if data['cm_nramp']==1:
                data['cm_ramp'] = data['cm_ramp'][0]
            else:
                data['cm_ramp'] = ", ".join([str(p) for p in data['cm_ramp']])
            data['cm_sqf'] = ZZ(self.cm).squarefree_part()

            data['CM'] = "yes (\(D=%s\))" % data['CMD']
            if data['CMD']%4==0:
                d4 = ZZ(data['CMD'])//4
                data['EndE'] = "\(\Z[\sqrt{%s}]\)" % d4
            else:
                data['EndE'] = "\(\Z[(1+\sqrt{%s})/2]\)" % data['CMD']
            data['ST'] = st_link_by_name(1,2,'N(U(1))')
        else:
            data['ST'] = st_link_by_name(1,2,'SU(2)')

        data['p_adic_primes'] = [p for i,p in enumerate(prime_range(5, 100))
                                 if (N*data['ap'][i]) %p !=0]

        cond, iso, num = split_lmfdb_label(self.lmfdb_label)
        self.class_url = url_for(".by_double_iso_label", conductor=N, iso_label=iso)
        self.one_deg = ZZ(self.class_deg).is_prime()
        self.ncurves = db_ec().count({'lmfdb_iso':self.lmfdb_iso})
        isodegs = [str(d) for d in self.isogeny_degrees if d>1]
        if len(isodegs)<3:
            data['isogeny_degrees'] = " and ".join(isodegs)
        else:
            data['isogeny_degrees'] = " and ".join([", ".join(isodegs[:-1]),isodegs[-1]])


        if self.twoadic_gens:
            from sage.matrix.all import Matrix
            data['twoadic_gen_matrices'] = ','.join([latex(Matrix(2,2,M)) for M in self.twoadic_gens])
            data['twoadic_rouse_url'] = ROUSE_URL_PREFIX + self.twoadic_label + ".html"

        # Leading term of L-function & BSD data
        bsd = self.bsd = {}
        r = self.rank
        if r >= 2:
            bsd['lder_name'] = "L^{(%s)}(E,1)/%s!" % (r,r)
        elif r:
            bsd['lder_name'] = "L'(E,1)"
        else:
            bsd['lder_name'] = "L(E,1)"

        bsd['reg'] = self.regulator
        bsd['omega'] = self.real_period
        bsd['sha'] = int(0.1+self.sha_an)
        bsd['lder'] = self.special_value

        # Optimality (the optimal curve in the class is the curve
        # whose Cremona label ends in '1' except for '990h' which was
        # labelled wrongly long ago)

        if self.iso == '990h':
            data['Gamma0optimal'] = bool(self.number == 3)
        else:
            data['Gamma0optimal'] = bool(self.number == 1)


        data['p_adic_data_exists'] = False
        if data['Gamma0optimal']:
            data['p_adic_data_exists'] = (padic_db().find({'lmfdb_iso': self.lmfdb_iso}).count()) > 0

        data['iwdata'] = []
        try:
            pp = [int(p) for p in self.iwdata]
            badp = [l['p'] for l in self.local_data]
            rtypes = [l['red'] for l in self.local_data]
            data['iw_missing_flag'] = False # flags that there is at least one "?" in the table
            data['additive_shown'] = False # flags that there is at least one additive prime in table
            for p in sorted(pp):
                rtype = ""
                if p in badp:
                    red = rtypes[badp.index(p)]
                    # Additive primes are excluded from the table
                    # if red==0:
                    #    continue
                    #rtype = ["nsmult","add", "smult"][1+red]
                    rtype = ["nonsplit","add", "split"][1+red]
                p = str(p)
                pdata = self.iwdata[p]
                if isinstance(pdata, type(u'?')):
                    if not rtype:
                        rtype = "ordinary" if pdata=="o?" else "ss"
                    if rtype == "add":
                        data['iwdata'] += [[p,rtype,"-","-"]]
                        data['additive_shown'] = True
                    else:
                        data['iwdata'] += [[p,rtype,"?","?"]]
                        data['iw_missing_flag'] = True
                else:
                    if len(pdata)==2:
                        if not rtype:
                            rtype = "ordinary"
                        lambdas = str(pdata[0])
                        mus = str(pdata[1])
                    else:
                        rtype = "ss"
                        lambdas = ",".join([str(pdata[0]), str(pdata[1])])
                        mus = str(pdata[2])
                        mus = ",".join([mus,mus])
                    data['iwdata'] += [[p,rtype,lambdas,mus]]
        except AttributeError:
            # For curves with no Iwasawa data
            pass

        tamagawa_numbers = [ZZ(ld['cp']) for ld in local_data]
        cp_fac = [cp.factor() for cp in tamagawa_numbers]
        cp_fac = [latex(cp) if len(cp)<2 else '('+latex(cp)+')' for cp in cp_fac]
        bsd['tamagawa_factors'] = r'\cdot'.join(cp_fac)
        bsd['tamagawa_product'] = prod(tamagawa_numbers)

        # Torsion growth data

        data['torsion_growth_data_exists'] = False
        try:
            tg = self.tor_gro
            data['torsion_growth_data_exists'] = True
            data['tgx'] = tgextra = []
            # find all base-changes of this curve in the database, if any
            bcs = [res['label'] for res in  getDBConnection().elliptic_curves.nfcurves.find({'base_change': self.lmfdb_label}, projection={'label': True, '_id': False})]
            bcfs = [lab.split("-")[0] for lab in bcs]
            for F, T in tg.items():
                tg1 = {}
                tg1['bc'] = "Not in database"
                if ":" in F:
                    F = F.replace(":",".")
                    field_data = nf_display_knowl(F, getDBConnection(), field_pretty(F))
                    deg = int(F.split(".")[0])
                    bcc = [x for x,y in zip(bcs, bcfs) if y==F]
                    if bcc:
                        from lmfdb.ecnf.main import split_full_label
                        F, NN, I, C = split_full_label(bcc[0])
                        tg1['bc'] = bcc[0]
                        tg1['bc_url'] = url_for('ecnf.show_ecnf', nf=F, conductor_label=NN, class_label=I, number=C)
                else:
                    field_data = web_latex_split_on_pm(coeff_to_poly(string2list(F)))
                    deg = F.count(",")
                tg1['d'] = deg
                tg1['f'] = field_data
                tg1['t'] = '\(' + ' \\times '.join(['\Z/{}\Z'.format(n) for n in T.split(",")]) + '\)'
                tg1['m'] = 0
                tgextra.append(tg1)

            tgextra.sort(key = lambda x: x['d'])
            data['ntgx'] = len(tgextra)
            lastd = 1
            for tg in tgextra:
                d = tg['d']
                if d!=lastd:
                    tg['m'] = len([x for x in tgextra if x['d']==d])
                    lastd = d
            data['tg_maxd'] = max(db_ecstats().find_one({'_id': 'torsion_growth'})['degrees'])

        except AttributeError:
            pass # we have no torsion growth data


        data['newform'] =  web_latex(PowerSeriesRing(QQ, 'q')(data['an'], 20, check=True))
        data['newform_label'] = self.newform_label = newform_label(cond,2,1,iso)
        self.newform_link = url_for("emf.render_elliptic_modular_forms", level=cond, weight=2, character=1, label=iso)
        self.newform_exists_in_db = is_newform_in_db(self.newform_label)
        self._code = None

        self.class_url = url_for(".by_double_iso_label", conductor=N, iso_label=iso)
        self.friends = [
            ('Isogeny class ' + self.lmfdb_iso, self.class_url),
            ('Minimal quadratic twist %s %s' % (data['minq_info'], data['minq_label']), url_for(".by_triple_label", conductor=minq_N, iso_label=minq_iso, number=minq_number)),
            ('All twists ', url_for(".rational_elliptic_curves", jinv=self.jinv)),
            ('L-function', url_for("l_functions.l_function_ec_page", conductor_label = N, isogeny_class_label = iso))]
        if not self.cm:
            if N<=300:
                self.friends += [('Symmetric square L-function', url_for("l_functions.l_function_ec_sym_page", power='2', conductor = N, isogeny = iso))]
            if N<=50:
                self.friends += [('Symmetric cube L-function', url_for("l_functions.l_function_ec_sym_page", power='3', conductor = N, isogeny = iso))]
        if self.newform_exists_in_db:
            self.friends += [('Modular form ' + self.newform_label, self.newform_link)]

        self.downloads = [('Download coefficients of q-expansion', url_for(".download_EC_qexp", label=self.lmfdb_label, limit=1000)),
                          ('Download all stored data', url_for(".download_EC_all", label=self.lmfdb_label)),
                          ('Download Magma code', url_for(".ec_code_download", conductor=cond, iso=iso, number=num, label=self.lmfdb_label, download_type='magma')),
                          ('Download Sage code', url_for(".ec_code_download", conductor=cond, iso=iso, number=num, label=self.lmfdb_label, download_type='sage')),
                          ('Download GP code', url_for(".ec_code_download", conductor=cond, iso=iso, number=num, label=self.lmfdb_label, download_type='gp'))
        ]

        try:
            self.plot = encode_plot(self.E.plot())
        except AttributeError:
            self.plot = encode_plot(EllipticCurve(data['ainvs']).plot())

        self.plot_link = '<a href="{0}"><img src="{0}" width="200" height="150"/></a>'.format(self.plot)
        self.properties = [('Label', self.lmfdb_label),
                           (None, self.plot_link),
                           ('Conductor', '\(%s\)' % data['conductor']),
                           ('Discriminant', '\(%s\)' % data['disc']),
                           ('j-invariant', '%s' % data['j_inv_latex']),
                           ('CM', '%s' % data['CM']),
                           ('Rank', '\(%s\)' % mw['rank']),
                           ('Torsion Structure', '\(%s\)' % mw['tor_struct'])
                           ]

        self.title = "Elliptic Curve %s (Cremona label %s)" % (self.lmfdb_label, self.label)

        self.bread = [('Elliptic Curves', url_for("ecnf.index")),
                           ('$\Q$', url_for(".rational_elliptic_curves")),
                           ('%s' % N, url_for(".by_conductor", conductor=N)),
                           ('%s' % iso, url_for(".by_double_iso_label", conductor=N, iso_label=iso)),
                           ('%s' % num,' ')]

    def code(self):
        if self._code == None:
            self.make_code_snippets()
        return self._code

    def make_code_snippets(self):
        # read in code.yaml from current directory:

        _curdir = os.path.dirname(os.path.abspath(__file__))
        self._code =  yaml.load(open(os.path.join(_curdir, "code.yaml")))

        # Fill in placeholders for this specific curve:

        for lang in ['sage', 'pari', 'magma']:
            self._code['curve'][lang] = self._code['curve'][lang] % (self.data['ainvs'],self.label)
        return
        for k in self._code:
            if k != 'prompt':
                for lang in self._code[k]:
                    self._code[k][lang] = self._code[k][lang].split("\n")
                    # remove final empty line
                    if len(self._code[k][lang][-1])==0:
                        self._code[k][lang] = self._code[k][lang][:-1]
