#!/usr/bin/env python3

""" create_data.py -

- This script takes 2 files:

    1)  free-zipcode-database-Primary.no.header.csv - government zip DB
        (From http://federalgovernmentzipcodes.us/download.html)

"02108","STANDARD","BOSTON","MA","PRIMARY",42.35,-71.06,"NA-US-MA-BOSTON","false",2348,3312,388783474
"02109","STANDARD","BOSTON","MA","PRIMARY",42.35,-71.06,"NA-US-MA-BOSTON","false",2966,4145,284385612
"02110","STANDARD","BOSTON","MA","PRIMARY",42.35,-71.06,"NA-US-MA-BOSTON","false",2950,4313,231268950

   2) craigs_links.txt - City, State names gotten from  craigslist.org
     (From https://geo.craigslist.org/iso/us/)

<li><a href="https://akroncanton.craigslist.org">akron / canton</a></li>
<li><a href="https://albanyga.craigslist.org">albany, GA      </a></li>

And creates 2 dictionaries from those 2 files:

    1) Government File -  City,State to MultiZip dict

    which is a default dictionary with (city,STATE) as key
    and list of zipcodes as values:

    {'abilene,TX':   ['79604'],
    'wiley,GA':      ['30581'],
    'woodstock,GA':  ['30188', '30189']}

    and

    2) City,State to craigslist url dict - IE:

    {'abilene,TX':   'https://abilene.craigslist.org',
     'akron/canton': 'https://akroncanton.craigslist.org',
     'albany,NY': 'https://albany.craigslist.org'}

i.e.
craigs_city_links = create_craigs_url_dict_from_local_file(craigs_links_file)
gov_city_state_mutlizips_map = create_gov_city_state_mutlizips_map(zip_code_file)


- Then  creates another dictionary of mean/average zip code to craigurl:

{ 28541: 'https://onslow.craigslist.org',
  28406: 'https://wilmington.craigslist.org',
  27154: 'https://winstonsalem.craigslist.org'}

(These will all be KNOWN URLS in the craigslist url map - used below)


i.e
mean_zip2craigs_url = create_mean_zipcode_2_craigs_url_map(craigs_city_links, gov_city_state_mutlizips_map)

- Finally a list of 400+ documents are prepared by creating a Mongo Fomatted
Document for each Craigsurl.

  Then, each city,state and zip code list from the government dictionary will try to be
matched into one of the 400+  Mongo Documents by comparing city,state.

If there's a match, the document will be populated with all the zip codes. We
mark these as 'Zips' in the list as they are primary.

If there's not a match, we find the closest zip code and craiglist url known
using the mean/average zip code dictionary and populate that document with the
zip code data. We mark these as 'ALtZips' in the list as they are not primary zips.

i.e
master_mongo_city_state_zip_data = generate_master_documents_import_to_mongodb(
craigs_city_links, gov_city_state_mutlizips_map, mean_zip2craigs_url)

Once  done, there will be 400+ MongoDB documents for initial load. (There are
~400 total craiglist urls). Most of the 400 are populated with zip code data.
The other ~30 do not have any data at all (for now). see update_craigs_links.py

However all ZIP CODES map to one of 200 urls. So given a given a query for any
zip code, some data will be returned if the corresponding craigs url has
been crawled and indexed.

This is 'imperfect' data, but at least all of the zips in the government file will
have relevant data from somewhere 'somewhat' close. This also means a zip codes
be considered 'close' to city that is a decent distance away as the crow flies.

To avoid duplicate docs, create_data.py must be run before crawler.py runs.

The latter does upserts where the former just does inserts (it is run as
entrypoint to the docker image - so should never be a problem).

"""


import os
import sys
import logging

import csv
import statistics

from collections import defaultdict
from pymongo.errors import ConnectionFailure
from pymongo.errors import BulkWriteError
from pymongo.errors import OperationFailure


URL = "http://federalgovernmentzipcodes.us/download.html"  # not used
my_file_name = os.path.basename(__file__)
zip_code_file = "../data/free-zipcode-database-Primary.no.header.csv"
craigs_links_file = "../data/craigs_links.txt"


def create_gov_city_state_mutlizips_map(zip_code_file):
    """
    Return a default dictionary with (city,state) as key and
    and a list of zipcodes as values.

    Parameters
    ----------
    zip_code_file : file
        File at URL
    Returns:
        default dictionary - gov_city_state_zips
    """
    gov_city_state_zips = defaultdict(list)
    with open(zip_code_file) as csv_fh:
        csv_reader = csv.reader(csv_fh, delimiter=",")
        for row in csv_reader:
            zipc = row[0]
            city = row[2]
            city = city.lower()
            city = " ".join(city.split())  # no spaces
            state = row[3]
            state = " ".join(state.split())
            state = state.upper()
            gov_city_state_zips[f"{city},{state}"].append(zipc)
        return gov_city_state_zips


def create_craigs_url_dict_from_local_file(craigs_links_file):
    """
    Return a dictionary (city,state => url) given  craigs_links_file file

    Parameters
    ----------
    craigs_links_file : file
        Boston, MA : boston.craiglist.com
    Returns:
        {dictionary}
    """
    with open(craigs_links_file) as fh:
        contents = fh.readlines()
        craigs_city_links = {}
        for line in contents:
            citytext, craigs_link = line.split("=")
            craigs_link = "".join(craigs_link.split())
            craigs_city_links[citytext] = craigs_link

    return craigs_city_links


def lookup_craigs_url_with_city(citystate, craigs_city_links):
    """
    Return a Craigslist URL  given city,state and url map

    Parameters
    ----------
    citystate : str
        Boston,MA
    craigs_city_links : dictionary
        Craiglist url to city  map

    Returns
    -------
        str - boston.craiglist.com
    """
    return craigs_city_links[citystate]


def create_mean_zipcode_2_craigs_url_map(craigs_city_links, gov_city_state_zips):
    """ Return the mean of the zips associated with a craigsurl

    Parameters
    ----------
    craigs_city_links : dictionary
        Craiglist url to city  map
    gov_city_state_zip :
        city to zip lists

    Returns
    -------
        {dictionary} - meanzip : Craiglist url
    """
    mean_zip2craigs_url = {}

    for citystate, ziplist in gov_city_state_zips.items():
        citystate = "".join(citystate.split())
        try:
            ziplist = [int(x) for x in ziplist]
            craigs_url = lookup_craigs_url_with_city(citystate, craigs_city_links)
            mean = round(statistics.median(ziplist))
            if len(str(mean)) == 4:
                mean = str(0) + str(mean)
            mean_zip2craigs_url[mean] = craigs_url
        except KeyError:
            # We don't care about these at all because we KNOW these
            # are cities that don't match, so we let it pass
            pass

    return mean_zip2craigs_url


def find_closest_craigs_url_for_other_cities(zip, mean_zip2craigs_url):
    """ Given a zipcode and meanzip : Craiglist url map return Craigslist URL
        closest to a town

        Parameters
        ----------
        zip : str
            zipcode
        mean_zip2craigs_url :
            {dictionary} - meanzip : Craiglist url

        Returns:
        --------
            str - Craiglist url - craiglist.com
    """
    if zip[0] == 0:
        zip = zip[1:]
    try:
        closest = mean_zip2craigs_url[
            min(mean_zip2craigs_url.keys(), key=lambda k: abs(int(k) - int(zip)))
        ]
        return closest
    except TypeError as e:
        raise ValueError(e)


def generate_master_documents_import_to_mongodb(
    craigs_city_links, gov_city_state_zips, mean_zip2craigs_url, verbose=False
):
    """ Format MongoDB documents and return a list of 400+ of them

    Parameters
    ----------
    craigs_city_links :
        dictionary above
    gov_city_state_zip :
        {dictionary}  above
    mean_zip2craigs_url :
        {dictionary} above

    Returns:
        [list] - All mongodb documents will initially have this format:

    {
    'craigs_list_url': 'https://zanesville.craigslist.org',
    'CityState': None,
    'Zips': [],
    'AltZips': [],
    'AltCities': []
    }
    """

    master_mongo_city_state_zip_data = []
    master_mongo_city_state_zip_map = {}

    for url in craigs_city_links.values():

        master_mongo_city_state_zip_map[url] = {"craigs_url": url}
        master_mongo_city_state_zip_map[url]["CityState"] = None
        master_mongo_city_state_zip_map[url]["Zips"] = []
        master_mongo_city_state_zip_map[url]["AltZips"] = []
        master_mongo_city_state_zip_map[url]["AltCities"] = []

    for citystate, ziplist in gov_city_state_zips.items():

        try:
            citystate = "".join(citystate.split())
            url = lookup_craigs_url_with_city(citystate, craigs_city_links)
            master_mongo_city_state_zip_map[url]["CityState"] = citystate
            master_mongo_city_state_zip_map[url]["Zips"] = ziplist
        except KeyError:
            # continue
            first_zip = ziplist[0]
            url = find_closest_craigs_url_for_other_cities(
                first_zip, mean_zip2craigs_url
            )
            master_mongo_city_state_zip_map[url]["AltZips"].extend(ziplist)
            master_mongo_city_state_zip_map[url]["AltCities"].append(citystate)
        except Exception as e:
            logging.exception("Caught an error")
        # If we print out  or iterate here, we see it building vs the end state

    # If we print out or iterate here,  life is good:
    for url in craigs_city_links.values():
        if verbose:
            print(master_mongo_city_state_zip_map[url])
        master_mongo_city_state_zip_data.append(master_mongo_city_state_zip_map[url])

    return master_mongo_city_state_zip_data


if __name__ == "__main__":

    import sys
    import mongodb

    try:
        craigs_city_links = create_craigs_url_dict_from_local_file(craigs_links_file)
        gov_city_state_mutlizips_map = create_gov_city_state_mutlizips_map(
            zip_code_file
        )
        mean_zip2craigs_url = create_mean_zipcode_2_craigs_url_map(
            craigs_city_links, gov_city_state_mutlizips_map
        )
        master_mongo_city_state_zip_data = generate_master_documents_import_to_mongodb(
            craigs_city_links, gov_city_state_mutlizips_map, mean_zip2craigs_url
        )
        print(master_mongo_city_state_zip_data)
        mongo_cli = mongodb.MongoCli()
        mongo_cli.init_load_city_state_zip_map(master_mongo_city_state_zip_data)
    except FileNotFoundError as e:
        print("File Not Found", e)
    except ConnectionFailure as e:
        print("Connection Failure: ", e)
    except BulkWriteError as e:
        print("BulkWrite Error: ", e)
    except OperationFailure as e:  # This includes Bad Authentication
        print("Unanticipated Mongo Error: ", e)
    except Exception as e:
        logging.exception(f"Unhandled Error: {e}")
    else:
        print("Successfully entered into Mongodb")
