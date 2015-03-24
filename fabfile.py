import sys, urllib
from datetime import datetime, timedelta
from os import listdir, stat
from os.path import exists, join
from fabric.api import env, hosts, sudo, with_settings, cd, local, lcd, put, abort
from fabric.context_managers import settings
from fabric.contrib import files, django
from fabric.contrib.console import confirm
from fabric.operations import prompt
from django.utils import translation

# ssh -i /lfw/lendfriend/deployment/dkuchar.pem ubuntu@50.18.114.99

PROJECT_DIR_LOCAL = '/lfw/lendfriend'
PROJECT_DIR_PRODUCTION = '/lf'
PROJECT_DIR_PRODUCTION_ABS = '/home/lendfriend'

sys.path.append(PROJECT_DIR_LOCAL)
sys.path.append(PROJECT_DIR_PRODUCTION)
django.project('lendfriend')

from django.conf import settings as project_settings

PRODUCTION_HOSTS = '50.18.114.99'#['lendfriend.com']
PRODUCTION_IP = '50.18.114.99'

# PRODUCTION_HOSTS = '184.72.53.35'#['lendfriend.com']
# PRODUCTION_IP = '184.72.53.35'

STAGING_HOSTS = '50.18.46.172'#['staging.lendfriend.com']
STAGING_IP = '50.18.46.172'

env.user = "ubuntu"
env.key_filename = ["deployment/dkuchar.pem"]

AMI_IMAGE = "ami-f5bfefb0"

TEMP_DIR_LOCAL = "/tmp/lendfriend_stage"
TEMP_PACKAGE_LOCAL = "/tmp/lendfriend.tgz"
TEMP_DIR_PRODUCTION = '/tmp/'

def str2bool(v):
	if type(v) is str:
		return v.lower() in ("yes", "true", "t", "1")
	else:
		return v

##################################
## install_modules
##################################

@hosts(PRODUCTION_HOSTS)
def install_modules_live():
	install_modules('production')

@hosts(STAGING_HOSTS)
def install_modules_stage():
	install_modules('staging')

def install_modules(deploy_to='dev'):
	if deploy_to == 'dev':
		with lcd(PROJECT_DIR_LOCAL):
			local('sudo pip install django')
			local('export PATH=$PATH:/usr/local/mysql/bin/')
			local('sudo pip install mysql-python')
			local('sudo pip install mercurial')
			local('sudo pip install django-compressor')
			local('sudo pip install -r requirements/dev.txt')
	else:
		with cd(PROJECT_DIR_PRODUCTION):
			sudo('pip install django')
			sudo('pip install mercurial')
			sudo('pip install django-compressor')
			sudo('pip install -r requirements/common.txt')

##################################
## Deployment
##################################

@hosts(PRODUCTION_HOSTS)
def deploylive(static=True, db=True):
	if confirm('Are you sure?', default=True):
		deploy('production', str2bool(static), str2bool(db))

@hosts(STAGING_HOSTS)
def deploystage(static=True, db=True):
	deploy('staging', str2bool(static), str2bool(db))

def deploy(deploy_to, static, db):
	#with lcd(PROJECT_DIR_LOCAL):
	#    test(verbose=False)

	start_time = datetime.now()
	deploy_base()

	install_maxmind_db()

	install_locales()

	pack(deploy_to)

	if static:
		deploy_static(deploy_to)
	deploy_static_time = datetime.now()

	deploy_web(deploy_to, db)
	deploy_done_time = datetime.now()

	#test(run_local=False)
	end_time = datetime.now()
	print_times(start_time, deploy_done_time, deploy_static_time, end_time)

def deploy_base():
	sudo('apt-get update -y')
	sudo('apt-get dist-upgrade -y')
	sudo('aptitude safe-upgrade -y')
	sudo('aptitude update -y')
	sudo('aptitude install -y munin-node munin-plugins-extra')
	sudo('aptitude install -y subversion git-core mercurial')
	sudo('aptitude install -y python-setuptools')
	sudo('aptitude install -y libapache2-mod-wsgi')
	sudo('aptitude install -y pngcrush')
	sudo('aptitude install -y python-dev python-imaging')
	sudo('aptitude install -y python-mysqldb')
	sudo('easy_install -U distribute')
	sudo('easy_install -U pip')
	sudo('easy_install -U supervisor')
	sudo('apt-get remove -y python-boto')

@with_settings(warn_only=True)
def deploy_web(deploy_to, db):
	if not files.exists('{0}/'.format(PROJECT_DIR_PRODUCTION_ABS)):
		sudo('mkdir {0}'.format(PROJECT_DIR_PRODUCTION_ABS))
		sudo('ln -s {0}/ {1}'.format(PROJECT_DIR_PRODUCTION_ABS, PROJECT_DIR_PRODUCTION))

	with cd(PROJECT_DIR_PRODUCTION):
		with settings(warn_only=True):
			sudo('rm -R *')
		sudo('tar xzf {0}'.format(TEMP_PACKAGE_LOCAL))

		install_modules(deploy_to)

		if db:
			install_db(deploy_to)

		# apache conf
		sudo('cp deployment/apache/default /etc/apache2/sites-available/default')
		sudo('rm /etc/apache2/sites-enabled/*')
		sudo('ln -s /etc/apache2/sites-available/default /etc/apache2/sites-enabled/000-default')
		sudo('cp deployment/apache/apache2.conf /etc/apache2/apache2.conf')

		sudo('a2enmod rewrite')
		sudo('apache2ctl restart')

		# supervisor conf
		sudo('cp -r deployment/supervisord* /etc/')
		sudo('supervisord -c /etc/supervisord.conf')

##################################
## Update Deployment
##################################

def updateall(modules=True, static=True):
	updatelive(modules,static)
	updatestage(modules,static)

@hosts(PRODUCTION_HOSTS)
def updatelive(description="", modules=True, static=True):
	if confirm('Are you sure?', default=True):
		modules = confirm("Update Modules?", default=True)
		static = confirm("Update Static Files?", default=True)
		description = prompt("Enter description of changes made:")
		update('production', str2bool(modules), str2bool(static))
		version_commit(description)

@hosts(STAGING_HOSTS)
def updatestage(modules=True, static=True):
	update('staging', str2bool(modules), str2bool(static))

def update(deploy_to, modules=False, static=False):
	# with lcd(PROJECT_DIR_LOCAL):
	#    test(verbose=False)
	start_time = datetime.now()

	if maxmind_expired():
		install_maxmind_db()

	pack(deploy_to)

	if static:
		deploy_static(deploy_to)
	deploy_static_time = datetime.now()

	update_web(deploy_to, modules)
	deploy_done_time = datetime.now()

	update_daemon(deploy_to)

	# test(run_local=False)
	end_time = datetime.now()
	print_times(start_time, deploy_done_time, deploy_static_time, end_time)

@with_settings(warn_only=True)
def update_web(deploy_to, modules=False):
	with cd(PROJECT_DIR_PRODUCTION):
		sudo('rm -R *')
		sudo('tar xzf {0}'.format(TEMP_PACKAGE_LOCAL))

		if modules:
			install_modules(deploy_to)

		install_db(deploy_to)

		sudo('cp deployment/apache/default /etc/apache2/sites-available/default')
		sudo('apache2ctl graceful')

@with_settings(warn_only=True)
def update_daemon(deploy_to):
	with cd(PROJECT_DIR_PRODUCTION):
		sudo('cp -r deployment/supervisord* /etc/')

		status = sudo('supervisorctl status')
		if status == 'unix:///var/run/supervisor.sock refused connection':
			sudo('unlink /var/run/supervisor.sock')
			sudo('supervisord -c /etc/supervisord.conf')
		else:
			sudo('supervisorctl reread')
			sudo('supervisorctl update')

		sudo('supervisorctl tail celeryd')
		sudo('supervisorctl restart celeryd')

##################################
## Deployment Helpers
##################################

@with_settings(warn_only=True)
def pack(deploy_to):
	if exists(TEMP_DIR_LOCAL):
		print "temp directory exists"
		if listdir(TEMP_DIR_LOCAL) != []:
			print "files exist in temp directory"
			local('sudo rm -R {0}/*'.format(TEMP_DIR_LOCAL))
	else:
		print "temp directory doesn't exist"
		local('mkdir {0}'.format(TEMP_DIR_LOCAL))

	if exists(TEMP_PACKAGE_LOCAL):
		print "temp package exists"
		local('sudo rm -R {0}'.format(TEMP_PACKAGE_LOCAL))

	with lcd(PROJECT_DIR_LOCAL):
		local('find . -name "*.pyc" -delete')
		local('find . -name "*site*.comp*" -delete')

	with lcd(TEMP_DIR_LOCAL):
		local('cp -R {0}/* ./'.format(PROJECT_DIR_LOCAL))
		local('rm sqlite.db')
		local('rm deployment/*.pem')
		local('rm lendfriend/website/fixtures/initial_data_development.json')
		local('rm lendfriend/settings/dev.py')

		if deploy_to == 'staging':
			local('rm lendfriend/website/fixtures/initial_data_production.json')
			local('rm lendfriend/settings/production.py')
			local('rm deployment/apache/default')
			local('mv deployment/apache/staging/default deployment/apache/default')
		else:
			local('rm lendfriend/website/fixtures/initial_data_staging.json')
			local('rm lendfriend/settings/staging.py')

		local('find . -name "*.pyc" -delete')
		local('tar czf {0} .'.format(TEMP_PACKAGE_LOCAL))

	put(TEMP_PACKAGE_LOCAL, TEMP_DIR_PRODUCTION)

def print_times(start_time, deploy_done_time, deploy_static_time, end_time):
	deploy_time = deploy_done_time - deploy_static_time
	static_time = deploy_static_time - start_time
	test_time = end_time - deploy_done_time
	total_time = end_time - start_time
	print ""
	print "*" * 80
	print " Deploy took:    %s seconds" % str(deploy_time.seconds)
	print " CDN Push took:  %s seconds" % str(static_time.seconds)
	print " Testing took:   %s seconds" % str(test_time.seconds)
	print " TOTAL:          %s seconds" % str(total_time.seconds)
	print "*" * 80
	print " Deploy Finished: %s Pacific Time" % end_time
	print "*" * 80

##################################
## deploy_static
##################################

@hosts(PRODUCTION_HOSTS)
def deploy_static_live():
	deploy_static('production')

@hosts(STAGING_HOSTS)
def deploy_static_stage():
	deploy_static('staging')

def deploy_static(deploy_to='dev'):
	if deploy_to == 'dev':
		with lcd(PROJECT_DIR_LOCAL):
			local('find . -name "*site*.comp*" -delete')
			local('python manage.py collectstatic --noinput')
			local('python manage.py bundle_media')
			local('python manage.py compress --extension=.haml,.html')
	else:
		with lcd(TEMP_DIR_LOCAL):
			local('find . -name "*site*.comp*" -delete')
			local('python manage.py collectstatic --noinput')
			local('python manage.py bundle_media')
			local('python manage.py compress --extension=.haml,.html')



##################################
## install_db
##################################

@hosts(PRODUCTION_HOSTS)
def install_db_live():
	install_db('production')

@hosts(STAGING_HOSTS)
def install_db_stage():
	install_db('staging')

def install_db(deploy_to='dev'):
	if deploy_to == 'dev':
		with lcd(PROJECT_DIR_LOCAL):
			local('python manage.py syncdb --noinput')
			local('python manage.py migrate transport 0001 --fake')
			local('python manage.py migrate --noinput')
			local('python manage.py loaddata lendfriend/website/fixtures/initial_data_development.json')
	else:
		with cd(PROJECT_DIR_PRODUCTION):
			sudo('python manage.py syncdb --noinput')
			sudo('python manage.py migrate transport 0001 --fake')
			sudo('python manage.py migrate --noinput')
			if deploy_to == 'staging':
				sudo('python manage.py loaddata lendfriend/website/fixtures/initial_data_staging.json')
			else:
				sudo('python manage.py loaddata lendfriend/website/fixtures/initial_data_production.json')

##################################
## localization
##################################

def update_lang():
	with lcd(PROJECT_DIR_LOCAL):
		for lang in project_settings.LANGUAGES:
			locale = translation.to_locale(lang[0])
			local('sudo python manage.py makemessages -l %s -e html,haml,py,txt' % locale)

def build_lang():
	with lcd(PROJECT_DIR_LOCAL):
		local('sudo python manage.py compilemessages')

def maxmind_expired():
	statbuf = stat(join(PROJECT_DIR_LOCAL,'geo/data/GeoLiteCity.dat'))
	last_maxmind_update = datetime.fromtimestamp(statbuf.st_mtime)
	days_since_last_update = (datetime.now() - last_maxmind_update).days
	print "Days since last maxmind update: " + str(days_since_last_update)
	return days_since_last_update > 30

def install_maxmind_db():
	with lcd(PROJECT_DIR_LOCAL):
		urllib.urlretrieve(
			'http://geolite.maxmind.com/download/geoip/database/GeoLiteCity.dat.gz',
			'geo/data/GeoLiteCity.dat.gz')
		local('sudo gunzip -f geo/data/GeoLiteCity.dat.gz')

		urllib.urlretrieve(
			'http://geolite.maxmind.com/download/geoip/database/GeoLiteCountry/GeoIP.dat.gz',
			'geo/data/GeoIP.dat.gz')
		local('sudo gunzip -f geo/data/GeoIP.dat.gz')

@hosts(PRODUCTION_HOSTS)
def install_locales_live():
	install_locales()

@hosts(STAGING_HOSTS)
def install_locales_stage():
	install_locales()

@with_settings(warn_only=True)
def install_locales():
	with cd('/usr/share/locales'):
		from geo.conf import settings as geo_settings
		for locale in geo_settings.LOCALES:
			sudo('./install-language-pack %s' % locale)
		sudo('dpkg-reconfigure locales')

##################################
## Local
##################################

def runserver(refresh=False):
	with lcd(PROJECT_DIR_LOCAL):
		local('sudo find . -name "*site*.comp*" -delete')
		local('sudo find . -name "*.pyc" -delete')
		if refresh:
			install_modules()
			update_lang()
			build_lang()
		if maxmind_expired():
			install_maxmind_db()
		local('python manage.py migrate --noinput')
		deploy_static()
		local('python manage.py cleanup')
		local('sudo python manage.py runserver 0.0.0.0:80')

def runcelery():
	with lcd(PROJECT_DIR_LOCAL):
		local('sudo python manage.py celeryd --verbosity=2 --loglevel=DEBUG')

def runcelerybeat():
	with lcd(PROJECT_DIR_LOCAL):
		local('sudo python manage.py celerybeat --verbosity=2 --loglevel=DEBUG')

def loginlive():
	local('ssh -i deployment/dkuchar.pem ubuntu@{0}'.format(PRODUCTION_IP))

def loginstage():
	local('ssh -i deployment/dkuchar.pem ubuntu@{0}'.format(STAGING_IP))

##################################
## Testing
##################################

APPS_TO_TEST = [
	"credit",
	"payments",
	"education",
	"home",
	"loans",
	"direct"
]

NOSE_ARGS = [
		'--tests=%s' % ",".join(APPS_TO_TEST),
		'--with-coverage',
		'--failfast',
]

fixtures_to_load = ["profiles/fixtures/profiles.json",
					"lendfriend/loans/fixtures/loans.json",
					"lendfriend/payments/fixtures/payments.json",
					"lendfriend/direct/fixtures/loan_requests.json"]

def test(run_local=True,verbose=True):
	if run_local:
		with lcd(PROJECT_DIR_LOCAL):
			if verbose:
				local('sudo python manage.py loaddata %s' % " ".join(fixtures_to_load))
				local('sudo python manage.py test %s --noinput' % " ".join(NOSE_ARGS))
			else:
				with settings(warn_only=True):
					result = local('sudo python manage.py test %s --noinput' % " ".join(NOSE_ARGS), capture=True)
				if result.failed and not confirm("Tests failed. Continue anyway?"):
					abort("Aborting at users request.")
	else:
		with cd(PROJECT_DIR_PRODUCTION):
			sudo('python manage.py test %s --noinput' % " ".join(NOSE_ARGS))

def test_legal():
	local('sudo python manage.py test --noinput legal')


##################################
## Version Control
##################################

def set_subl_as_editor():
	with lcd(PROJECT_DIR_LOCAL):
		local("git config --global core.editor \"subl -n -w\"")

def branch():
	with lcd(PROJECT_DIR_LOCAL):
		local("git branch")
		branch_name = prompt("Branch Name:")
		local("git checkout -b %s" % branch_name)
		local("git config push.default current")

def commit():
	with lcd(PROJECT_DIR_LOCAL):
		description = prompt("Enter description of changes made:")
		local("git add .")
		local("git commit -m \"%s\"" % description)

@hosts(PRODUCTION_HOSTS)
def squash():
	with lcd(PROJECT_DIR_LOCAL):
		local("git branch")
		branch_name = prompt("Branch Name:")
		local("git checkout master")
		local("git merge --squash %s" % branch_name)
		local("git commit -am \"Merge feature branch: %s\"" % branch_name) #-am?
		local("git branch -D %s" % branch_name)
	version_feature(branch_name)

def delete_branch():
	with lcd(PROJECT_DIR_LOCAL):
		local("git branch")
		branch_name = prompt("Branch Name:")
		local("git branch -D %s" % branch_name)

def push(branch_name=None):
	with lcd(PROJECT_DIR_LOCAL):
		if branch_name is None:
			local("git push origin")
		else:
			local("git push origin %s" % branch_name)

#
# from versioning import update_version_product, update_version_feature, update_version_commit
#
# @hosts(PRODUCTION_HOSTS)
# def version_product(description):
# 	version('product', description)
#
# @hosts(PRODUCTION_HOSTS)
# def version_feature(description):
# 	version('feature', description)
#
# @hosts(PRODUCTION_HOSTS)
# def version_commit(description):
# 	version('commit', description)
#
# def version(version_type, description):
# 	new_version = None
# 	description = description.replace(","," ")
#
# 	with cd(PROJECT_DIR_PRODUCTION):
# 		sudo("fab remote_update_version_%s:\"%s\"" % (version_type, description))
# 		new_version = sudo("cat version.txt")
#
# 	with lcd(PROJECT_DIR_LOCAL):
# 		if new_version is not None:
# 			local("git tag -a %s -m \"%s\"" % (str(new_version), description))
#
# def remote_update_version_product(description):
# 	new_version = update_version_product()
# 	with lcd(PROJECT_DIR_PRODUCTION):
# 		local("echo \"%s\" > version.txt" % new_version)
#
# def remote_update_version_feature(description):
# 	new_version = update_version_feature()
# 	with lcd(PROJECT_DIR_PRODUCTION):
# 		local("echo \"%s\" > version.txt" % new_version)
#
# def remote_update_version_commit(description):
# 	new_version = update_version_commit()
# 	with lcd(PROJECT_DIR_PRODUCTION):
# 		local("echo \"%s\" > version.txt" % new_version)
##



# <major version (marketing)>.<sub version>.<internal release version>.<>
# upgrade version when published to production

##################################
## OpenSSL
##################################

# sudo openssl genrsa -des3 -out staging.key 2048
# sudo openssl req -new -key staging.key -out staging.csr
